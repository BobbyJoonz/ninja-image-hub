# Agent development contract — Ninja Image Hub

Read **docs/CONCURRENCY_ARCHITECTURE_FA.md** before changing any orchestration or image code. It is the source of truth for concurrency, image batching, storage, failure modes, capacity and constraints. Read `REPORT_V3_FA.md` for completion status and known gaps.

## Non-negotiable invariants

1. Preserve multiple images from one request; don't return only `data[0]`.
2. The dimensions `account_count`, `jobs_per_account`, and `images_per_job` are **independent**. No multiplication yields guaranteed concurrent outputs.
3. Store queued jobs durably; never submit all waiting jobs to the Python ThreadPool at once.
4. Dispatch with global and per-account caps and round-robin fairness. Respect account cooldown. Do not switch accounts automatically after rate limits.
5. Never auto-retry upstream-uncertain requests; report `unknown`. Preserve partial image artifacts.
6. Require client `idempotency_key` reuse across ambiguous network retries. Return same job for same payload; HTTP 409 for conflicting payload.
7. Keep authentic browser sessions isolated per account; never log or export cookies, credentials, or worker API keys.
8. `Best`, `Balanced`, and `Draft` browser settings are **prompt guidance only**, not a native OpenAI API render quality setting. Preserve the original upstream bytes.
9. Preserve backward compatibility with v0.1/v0.2 SQL data and the one-image download route.
10. Single Uvicorn server process only: `--workers 1`, no `--reload` with active jobs.
11. No circumvention of ChatGPT rate limits, anti-abuse systems, MFA or CAPTCHA; documented source project warns that UI automation violates OpenAI terms. This is not a production SLA for Plus accounts.

## Tests and review

```bash
python -m pytest -q
node --check frontend/app.js
```

Unit and integration tests use mock Gateway and synthetic images; they MUST NOT be described as verified ChatGPT Plus image generation. An implementation change is not complete without updating API examples and concurrency guide, plus a negative test for the failed behavior.

## Future engineering roadmap

P0: test actual login and output compatibility **where allowed**, including upstream version pinning, error classification and safe security review.
P1: make queue worker independent of web UI, transactional PostgreSQL lease/heartbeats, image reconciliation, graceful cancellation and resource memory alerts.
P2: weighted-fair scheduling by measured work cost, per-provider policy caps, provider API abstraction, operational SLO dashboard, alerting and distributed storage.

## v0.4 operational governance (2026-11-03)

- Read `DIRECTION.md`, `docs/CONTROL_PLANE_V4_FA.md`, and `docs/ANALYTICS_DATA_DICTIONARY_FA.md` before editing admin/UI/scheduler.
- All editable policy settings MUST persist in `control_settings` and be validated server-side. Do not reintroduce `.env`-only knobs. Never change settings by simply displaying a fake form.
- Every control mutation must require admin session + CSRF and append an audit record without secrets. Do not expose stored API keys, cookies, CSRF, or PII in control audit.
- Settings applied in the dispatcher are live; Gateway env settings require worker restart. Show the difference; never restart busy workers as a side effect of Save.
- `dispatch_paused` and `accounts.dispatch_enabled=0` must never delete queued data or kill active jobs; resume must never reset `cooldown_until`.
- For analytics, count `job_images` by stored timestamp, not job count or submitted request count. Distinguish projection, observed throughput, and guaranteed SLA. Do not seed the live DB with fake statistics.
- One worker process per gateway account, one Uvicorn process overall. A production cluster requires distributed locks/leases and separate workers, NOT simply `uvicorn --workers 4`.
- Account plan labels remain manually entered and unverified. `Best/Balanced/Draft` are not actual API image-quality parameters.
- The provided `motion-primitives-main` components are React/TSX and have **not** been integrated directly into vanilla-JS production code. Avoid claiming otherwise. Icons must be sourced from an established library; do not hand-author SVG artwork. Check offline fallback and `prefers-reduced-motion`.
- UI changes require real Chromium offline-state screenshots at 1440 desktop and 390 mobile plus JS error/overflow checks. The mock browser should never be described as a live ChatGPT integration test.


## v0.5 modular implementation contract

- Read `docs/ARCHITECTURE_V5_FA.md` and `docs/FILE_PIPELINE_V5_FA.md` before modifying the new modules. v0.5 docs supersede conflicting historical layout details.
- Keep `app.py` an ASGI entrypoint. Use explicit per-application `ctx` dependencies; no exec-based module splitting, hidden globals or duplicated legacy monolith.
- Preserve original bytes; derived thumbnails are separate. Never substitute a preview for an original download.
- Keep 64 KiB control JSON, 32 MiB upstream JSON and 64 MiB individual-image budgets distinct. Chunked transport does not imply bounded total JSON or browser memory.
- Keep atomic no-clobber publication and database rollback cleanup. Acknowledge crash gaps between filesystem and SQL; audit tools must remain read-only unless a separate explicit destructive operation is requested.
- Preserve URL loopback + account-port allowlisting, no redirects, no environment proxy for local Gateway traffic, and route authentication on originals, thumbnails and exports.
- Return unknown on ambiguous submissions and response-budget/server failures; never generate again merely to obtain another response format.
- Gallery pagination uses immutable `(job_id, position)` ordering, not timestamp order. Do not claim it is chronological.
- Queue candidate retrieval must be bounded per account and must not starve a later account after max_pending_jobs is lowered.
- `preview/demo.js` must never be loaded by the real FastAPI page. No sample accounts, fake RAM or measurements in live SQL.
- Required checks: `python -m pytest -q`, `node --test tests/frontend.test.mjs`, and `node --check frontend/app.js`. Browser QA at 1440/390 CSS viewport must identify sample data; do not claim real Plus integration.

## v0.6 hardening contract (2026-11-06)

- The JS MIME pin in `hub/factory.py` must remain: `mimetypes.add_type("text/javascript", ".js")` and `.mjs` (plus JSON) are pinned at import. Removing it reintroduces the Windows `text/plain` regression that leaves the panel on the sign-in shell with zero `/api/*` requests.
- The worker environment allowlist must remain: the Node gateway starts from `WORKER_ENV_ALLOWLIST` via `worker_env()` only. Never pass `dict(os.environ)` (or the raw parent environment) to the gateway or its browser children; `APP_SECRET`, `ADMIN_PASSWORD`, `AGENT_API_KEY` and `DATABASE_URL` must never reach them.
- The SQLite settings must remain: `journal_mode=WAL`, `busy_timeout=15000`, `synchronous=NORMAL`, and a 15 s connect timeout. Do not remove them or delete `-wal`/`-shm` files while the process is running.
- A pre-send worker probe failure must **requeue** the job (`status=queued`, `started_at=None`), never terminally fail it. Nothing was submitted upstream, so retry is safe; the old behavior silently dropped jobs the provider never saw.
- The crash supervisor must respect `suppressed_workers`: an operator `stop_worker` must never be revived by `_supervise_workers`, and restart backoff stays 30/120/600/1800 s, and supervision gives up after 8 failed attempts (a manual Start clears the state and re-arms it; automatic starts must not override a suppressed operator stop). Keep loop order: supervise → refresh health → dispatch.
- Password and agent-key comparison must stay on UTF-8 bytes (`encode("utf-8")` before `hmac.compare_digest`); non-ASCII passphrases must return 200/401, never HTTP 500.
- `GET /api/health` must stay unauthenticated and limited to counters/booleans: no paths, prompts, account labels, API keys or cookies. Bump `version` (and the FastAPI version) with each release.
- Keep `scripts/doctor.py`, `scripts/serve_preview.py` and `tests/test_hardening.py` current with every change: new checks and scripts need regression coverage, and doctor must remain exit 0/1 with an actionable fix line.
- Keep `prepare_gateway.py` a complete install: lockfile refusal, pinned revision, `npm ci`, then both `npx playwright install chromium` and `npx patchright install chrome`; preserve `--yes`, `--skip-browsers`, the `codeload.github.com` tarball fallback when `github.com` is blocked, and the system-Chrome fallback when the Playwright download host is geo-blocked (the gateway defaults to patchright `channel=chrome`). `start_windows.bat`/`start_linux.sh` must keep creating `.env` on first run and serving port 8081 with `--workers 1`.
- Required checks are `python -m pytest -q` (**74 passed, 1 skipped**; the skip is allowed for the symlink test on unprivileged Windows), `node --test tests/frontend.test.mjs`, and `node --check` on `frontend/app.js`, all six `frontend/modules/` files and `preview/demo.js`.
- On-demand workers are the default: `idle_worker_shutdown_minutes` (control setting, default 20, 0 = always-on) stops idle browsers in `_reap_idle_workers`, and `_wake_for_queue` + the login page wake them again with exponential backoff (20s→300s). Never wake an account the operator stopped (it must stay queued), never reap an account with active or queued jobs, and keep the orphan reclaim scoped to the account's own workspace (kill the whole Node+Chrome tree). `GET /api/health` must keep reporting `running_workers`, `idle_workers` and `worker_policy`.
- Linux deployment: the browser must stay **headed** (never set `HEADLESS=true`), and each worker gets its own Xvfb through `hub/workers.worker_command()` / `display_mode()` — never share one display between accounts. Keep `deploy/install_linux.sh` and `deploy/ninja-image-hub.service` in sync with the install steps, keep the panel on 127.0.0.1 (SSH tunnel for access), and keep `docs/DEPLOY_LINUX_FA.md` accurate including the remote-login flow (Open ChatGPT → sign in → Save Session).
- Documentation changes do not require regenerating `MANIFEST_SHA256.json`; manifest regeneration is a release-owner step, not a docs task.
