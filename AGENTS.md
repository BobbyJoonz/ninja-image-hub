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
python -m pytest -q          # 112 passed, 1 skipped (skip = symlink test on unprivileged Windows)
node --test tests/frontend.test.mjs
node --check frontend/app.js # plus all frontend modules and preview/demo.js
python scripts/doctor.py     # exit 0 = ready
```

Unit and integration tests use mock gateways and synthetic images. They must never be described as verified ChatGPT Plus behaviour. An implementation change is not complete without updating the relevant doc and adding a negative test for the behaviour that used to fail.

## Repository hygiene

- No historical versioned documents, no sample databases, no fake statistics, no secrets in the tree (`.env`, `data/`, `vendor/` are ignored).
- `MANIFEST_SHA256.json` is a release seal regenerated by the release owner after the tree is final; it excludes `.env`, `data/`, `vendor/` and `backups/`. Use `python scripts/seal_manifest.py` (and `--check`), never a raw byte hash: text files are normalised CRLF→LF so Linux and Windows clones verify identically.
