# Agent development contract — Ninja Image Hub

Read these before changing behaviour, in order:

1. `docs/ARCHITECTURE_FA.md` — module layout, data flow, `ctx` composition.
2. `docs/FILE_PIPELINE_FA.md` — reception, storage, previews, ZIP, budgets.
3. `docs/OPERATIONS_FA.md` — capacity, failure modes, on-demand workers, monitoring.
4. `REPORT_FA.md` — current release state, evidence, known gaps.

The docs above are the current description. Older versioned documents were removed on purpose; do not re-introduce versioned copies (`REPORT_V5_FA.md`, `START_HERE_V3_FA.md`, …). Current state lives in the files listed here and in `README.md`.

## Non-negotiable invariants

1. Preserve every image of a multi-image request; never return only `data[0]`.
2. `account_count`, `jobs_per_account` and `images_per_job` are independent dimensions. No multiplication yields guaranteed concurrent output.
3. Store queued jobs durably; never submit all waiting jobs to the thread pool at once.
4. Dispatch with global and per-account caps, round-robin fairness and account cooldown. Do not switch accounts automatically after a rate limit.
5. Never auto-retry an upstream-uncertain request; report `unknown` and preserve partial artifacts.
6. Require `idempotency_key` reuse across ambiguous retries: same payload → same job, conflicting payload → HTTP 409.
7. Keep browser sessions isolated per account; never log or export cookies, credentials, CSRF tokens or worker API keys.
8. `Best` / `Balanced` / `Draft` are **prompt guidance only**, not upstream API quality parameters. Preserve original upstream bytes.
9. Keep backward compatibility with legacy SQL data and the one-image download route.
10. Single Uvicorn process: `--workers 1`, no `--reload` while jobs are active.
11. Never circumvent rate limits, anti-abuse systems, MFA or CAPTCHA. UI automation is unofficial and may breach provider terms; this is not a production SLA.

## Concurrency and dispatch

- Keep the queue state machine: `queued → running → completed | partial | failed | unknown | rate_limited | canceled`.
- A pre-send probe failure must **requeue** (`queued`, `started_at=None`), never terminally fail; nothing was submitted upstream.
- Health probes run outside the queue lock and in parallel; never do network I/O while holding `queue_guard`.
- The crash supervisor restarts autostart workers with 30/120/600/1800 s backoff, gives up after 8 attempts, and must respect `suppressed_workers` (an operator `stop_worker` must never be revived by the supervisor). Keep loop order: supervise → wake for queue → reap idle → refresh health → dispatch.
- On-demand workers are the default: `idle_worker_shutdown_minutes` (default 20, 0 = always-on) stops idle browsers; the next queued job or the login page wakes them with exponential backoff (20 s → 300 s). Never reap an account with active or queued jobs.
- Orphan reclaim must only touch processes that provably belong to that account's workspace, and must kill the whole Node + Chrome tree.
- Keep the job pool at least as large as the maximum `global_max_inflight` (currently 32), and keep `min_free_memory_mb` enforced before starting a browser.
- Keep the maintenance tick after `_refresh_health()` and before dispatch: supervise → wake → reap → refresh health → maintenance → dispatch.
- Maintenance may delete only derived/regenerable files (WebP previews, export ZIPs, `.incoming-*` spools, logs of **stopped** workers). Never originals, `gateway/auth/`, `auto-login.json`, the database or the audit. Audit only counts, never paths.
- A low disk (`min_free_disk_mb`) must pause **new** dispatch, keep queued jobs, and surface `disk_low`/`disk_free_mb` in `/api/health`; it must never delete data to make room.
- `POST /api/jobs/{id}/resolve` is the only reconciliation path: allowed only for `status="unknown"`, one-shot (`resolved_at` set), audited without the operator note, and it must never re-submit or rewrite status.

## Reasoning effort

- Every image generation must send the configured `thinking_mode` (default `high` → gateway
  `extended`) in the gateway payload. Never inherit the UI's previous tier and never treat
  effort as prompt guidance: it is a real ChatGPT web-UI control.
- Persist the gateway echo per job: `thinking_requested`, `thinking_applied`,
  `thinking_verified`, `thinking_fallback`, `thinking_mixed`. The applied tier is
  untrusted input; compare canonical tiers and treat a missing echo as unknown, never as
  success.
- A downgrade must be surfaced (job badge, 24h health counters, watchdog alert) and must
  never be retried, silently hidden, or converted into deleting/refusing a delivered
  image. `/api/health` exposes counts only.
- `thinking_effort` is a validated enum setting seeded by `THINKING_EFFORT`; unknown
  values fall back to the safe default (High), never below it.

## Reference uploads

- Validate every reference before storing it: count ≤ 10, magic bytes in {PNG, JPEG, WEBP},
  decodable, not animated, ≤ 16,777,216 pixels and within the configured per-file/total
  limits. Never trust the filename or the client's Content-Type.
- Preserve the uploaded original bytes exactly; never re-encode, resize or transcode them.
  Store them privately under `data/uploads/<uuid>/` (mode 0600) with a sha256 manifest and
  never log their contents.
- The 64 KiB JSON gate must stay for every other route; upload paths get one explicit cap
  derived from the live setting, and the route re-checks each file and the total before
  anything touches disk.
- A job references exactly one upload; `upload_id` is part of the idempotency fingerprint
  (same key + different upload → 409). A missing upload is a pre-send failure: fail the job
  with a clear error, never dispatch without the references.
- Terminal jobs delete their upload directory; `unknown` keeps it for reconciliation and
  the hourly sweep removes it after `upload_retention_hours`. The sweep must never touch an
  upload referenced by a queued/running job. Terminal cleanup must never delete gallery
  originals.
- Send references to the gateway as multipart `reference_images` only when present; the
  no-reference path stays the historical JSON request (tests and integrations patch that
  signature).
- Stream each upload to disk while hashing; never hold a whole request in memory. Reject a
  missing `Content-Length` with `411` (chunked multipart could fill the spool), and keep the
  directory at 0700/files at 0600.
- Multipart filenames must be unique per job (`<index>-<name>`); preserve the operator's
  display name only as metadata. Validate the stored `file` field in `meta.json` against the
  strict `<n>-<sha12>.<ext>` pattern before opening it.
- Keep `_patch_fast_prompt_insert` (marker `ninja:fast-prompt-insert`): it replaces the
  per-key typing path with `keyboard.insertText` when attachments must survive, and must stay
  fail-open (the upstream verification still falls back to typing). Never remove the patch or
  its idempotence marker; after an upgrade, workers must be Stop→Start-ed to receive it.
- Browser previews for references must stay bounded (96 px thumbnails); never hold ten
  decoded full-resolution images in the DOM. The uploaded File/bytes stay untouched.
- Reference downscaling is **opt-in** and client-side only (explicit `optimizeRefs` toggle,
  ≤2048 px WebP, only when the result is smaller). The server never re-encodes; with the
  toggle off the selected bytes are uploaded unchanged.
- `render_wait_sec` is a hard cap on the post-generation render wait (default 8, min 3, max
  60) passed to the gateway as `RENDER_WAIT_SEC`. It replaced a fixed 15 s sleep; the
  extraction path already polls until the image is usable, so lowering it is safe but must
  be re-verified after gateway upgrades. An adaptive DOM-readiness probe was tried and
  rejected: with backend read/canvas extraction the assistant image is not a stable
  full-size `<img>` early enough, so the probe only ever hit the cap.
- Canceling a queued job, deleting a settled/unknown job and purging an account must all
  release the job's upload workspace. Half-written `.incoming-*` workspaces are swept after
  one hour even when retention is disabled.

## Failure classification and operator tooling

- A gateway HTTP 5xx must be classified from its body (`error_kind`/`login_blocker`/message),
  not blanket `unknown`: page-load, login, composer-upload, validation, policy/refusal and
  explicit `generation_failed` outcomes are known failures and become `failed` with the real
  reason; only `timeout`, `server_error` and an unparseable/empty kind stay `unknown`. Never
  auto-retry either state.
- `scripts/resolve_unknown.py` is the only bulk reconciliation path: read-only by default,
  mutates only with `--yes`, only for still-unresolved `unknown` jobs. Resolving a job
  releases its reference workspace; the resolve endpoint remains one-shot and audited.
- `scripts/doctor.py` must warn when an installed worker workspace lacks the
  `ninja:fast-prompt-insert` marker or when a running gateway's `RENDER_WAIT_SEC` differs from
  the `render_wait_sec` setting; warnings must not fail the doctor (exit 0).
- Tests allocate worker ports from `GATEWAY_PORT_BASE` (default 41000; tests use 43000) so a
  live panel's workers cannot collide with a test run.

## File pipeline

- Preserve original bytes; previews are derived and separate. Never substitute a preview for an original download.
- Keep the budgets distinct: 64 KiB control JSON, 32 MiB upstream JSON envelope, 64 MiB per image, 24 MP preview decode cap, ZIP ≤ 10 files / 640 MiB.
- Keep atomic no-clobber publication (`os.link` after fsync) and database-rollback cleanup; acknowledge the crash gap between filesystem and SQL.
- Keep URL loopback + account-port allowlisting, no redirects, no environment proxy for local gateway traffic, and authentication on originals, previews and exports.
- Gallery pagination uses immutable `(job_id, position)` ordering — never claim it is chronological.
- `scripts/audit_gallery.py` stays read-only.

## Security

- Admin routers require the session cookie; every mutation additionally requires CSRF; `/v1/*` uses the Bearer agent key.
- `GET /api/health` stays unauthenticated and limited to counters/booleans: no paths, prompts, labels, keys or cookies.
- The worker environment comes from `WORKER_ENV_ALLOWLIST` via `worker_env()` only. Never pass `dict(os.environ)` to the gateway or its browser children.
- Password and agent-key comparisons use UTF-8 bytes; non-ASCII input must yield 401/403, never HTTP 500.
- Optional auto-login credentials live per account in `data/accounts/<id>/auto-login.json` (mode 0600), are passed to the worker only as `CHATGPT_EMAIL/PASSWORD/TOTP_SECRET`, are never returned by the API (status exposes `configured`/`has_totp` only) and are audited without secrets. Never log them or move them into the database.
- Keep SQLite `journal_mode=WAL`, `busy_timeout=15000`, `synchronous=NORMAL` and a 15 s connect timeout.
- Control mutations persist to `control_settings`, are validated server-side and audited without secrets. `.env` is not a live control plane.

## Code layout

- Gateway runtime patches live in `hub/gateway_patches.py` as an idempotent registry
  (`PATCH_ORDER`, `PATCH_MARKERS`, `apply_worker_patches`); the injected JS itself lives in
  `hub/patches/*.js` and is loaded read-only. Doctor imports the same marker names, never a
  literal. Worker child-process lifecycle is `hub/worker_process.py`; workspace copy, env
  allowlist, launch command and display are `hub/worker_setup.py`; diagnostics/status stay
  in `hub/workers.py`, which re-exports both for the runtime/tests.
- A job's single execution lives in `hub/execution.py`; the scheduler keeps dispatch,
  health, wake/reap and supervision. Failure classification lives in `hub/gateway_errors.py`.
- Shared test scaffolding lives in `tests/conftest.py` + `tests/helpers.py` (`build_hub`,
  `auth`, `fake_gateway`, `start_mock_account`, `all_css`). Do not copy fixtures or fake
  gateway servers into individual test modules.
- The version comes from `hub/version.py` (which reads `package.json`); never add a second
  version literal.
- Operator scripts share `scripts/_panel.py` for login/CSRF/JSON. Never duplicate that
  handshake in a new script. Capacity claims come only from `scripts/capacity_report.py`
  runs recorded in `docs/MEASUREMENTS_FA.md`; soak/session evidence comes from
  `scripts/soak.py`. Never describe arithmetic from software limits as measured capacity.
- Styles are `frontend/styles/base.css` + `features.css` (the UI links both); `style.css`
  remains an aggregating `@import` entry point for compatibility. Tests read `all_css()`.
- `frontend/app.js` is only the entrypoint: bootstrap/login/nav/account wiring lives in
  `modules/bootstrap.js`, generation in `modules/generate.js`, gallery/operations/jobs
  toolbars in their own modules, account dialogs in `account_login.js` /
  `resolve_dialog.js`, and each large view renderer in its own `*_view.js`. Do not grow
  app.js, accounts.js or views.js back into god files.
- `.github/workflows/ci.yml` runs static checks (ruff real errors, strict mypy for the pure
  modules in `pyproject.toml`, compile, JS syntax, node tests, icons, seal, offline test
  subset), the full suite with a coverage gate, a pip-audit of the runtime lock, and a
  headless browser smoke (8 views, zero exceptions, screenshot artifact) — all required.
  Reproducible installs use hash-pinned `requirements.lock` / `requirements-dev.lock`.

## Frontend and preview

- `frontend/` is native ES modules; the only entry point is `/static/app.js`. Keep `app.py` a thin ASGI entrypoint and compose routes explicitly in `hub/factory.py`.
- Pin the JavaScript MIME mapping in `hub/factory.py`; on some Windows hosts `.js` maps to `text/plain` and the dashboard never boots without it.
- `preview/demo.js` must never be loaded by the real FastAPI page. Sample data must be identifiable and never written to the live database.
- Icons come from the bundled Lucide sprite `frontend/icons/sprite.svg` (ISC, generated by `scripts/build_icons.py`). No CDN URLs, no Unicode glyph fallbacks and no hand-authored SVG artwork; keep `python scripts/build_icons.py --check` green and reference icons through `uiIcon()` / `<use href="/static/icons/sprite.svg#lc-NAME">`.
- Keep `preview/dist` byte-identical to `frontend/` (rebuild with `python preview/build.py`); `scripts/serve_preview.py` serves it with correct MIME types.
- Browser QA at 1440 and 390 CSS px must identify sample data; never describe the preview as a real provider integration.

## Linux deployment

- The browser stays **headed** (never set `HEADLESS=true`); each worker gets its own Xvfb via `worker_command()` / `display_mode()`. Never share one display between accounts.
- Keep `deploy/install_linux.sh` and `deploy/ninja-image-hub.service` in sync (single process, `--workers 1`, loopback bind, SSH tunnel for access).
- Keep `docs/LOGIN_FA.md` (three login methods, step by step) and `docs/DEPLOY_LINUX_FA.md` accurate; the remote-login flow is **Open ChatGPT → sign in → Save Session**.
- `deploy/ninja-backup.*` and `deploy/ninja-watch.service` are the operational timers; keep their paths, `.env` usage and the verified-backup `ExecStartPost` in sync with the scripts.
- `scripts/capacity_report.py` is the only accepted way to claim throughput; never turn its read-only snapshot into a performance claim, and never call mock-gateway tests real provider capacity.

## Required checks

```bash
python -m pytest -q          # 207 passed, 1 skipped (skip = symlink test on unprivileged Windows)
node --test tests/frontend.test.mjs
node --check frontend/app.js # plus all frontend modules and preview/demo.js
python scripts/doctor.py     # exit 0 = ready
```

Unit and integration tests use mock gateways and synthetic images. They must never be described as verified ChatGPT Plus behaviour. An implementation change is not complete without updating the relevant doc and adding a negative test for the behaviour that used to fail.

## Repository hygiene

- No historical versioned documents, no sample databases, no fake statistics, no secrets in the tree (`.env`, `data/`, `vendor/` are ignored).
- `MANIFEST_SHA256.json` is a release seal regenerated by the release owner after the tree is final; it excludes `.env`, `data/`, `vendor/` and `backups/`. Use `python scripts/seal_manifest.py` (and `--check`), never a raw byte hash: text files are normalised CRLF→LF so Linux and Windows clones verify identically.
