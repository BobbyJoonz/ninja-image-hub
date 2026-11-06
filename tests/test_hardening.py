"""Regression tests for the v0.6 hardening pass.

Every test here covers a behaviour that was broken in v0.5: the JS MIME mapping, parent
secrets leaking into the third-party worker environment, a pre-send probe failure
discarding a job, missing SQLite WAL, non-ASCII passphrases raising HTTP 500, a crashed
worker never coming back, and a non-reproducible preview build.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _build(monkeypatch, tmp_path, password="example-passphrase-123456789", db_url=None):
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    monkeypatch.setenv("APP_SECRET", "a" * 64)
    monkeypatch.setenv("AGENT_API_KEY", "test-agent-key-98765")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", db_url or "sqlite:///" + str(tmp_path / "hub.db"))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    ctx = module.app.state.hub
    ctx.app = module.app
    # Hardening tests must never launch a real gateway/browser, even on a machine where
    # vendor/ is installed. Tests that exercise supervision opt in explicitly.
    ctx.runtime_installed = lambda: False
    return module, ctx


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    module, ctx = _build(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        yield client, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def auth(client, password="example-passphrase-123456789"):
    res = client.post("/api/auth/login", json={"password": password})
    assert res.status_code == 200, res.text
    return {"x-csrf-token": res.json()["csrf"]}


def test_static_javascript_is_served_with_javascript_mime(loaded):
    """v0.5 regression: on Windows .js resolved to text/plain, so the SPA never booted."""
    client, _ = loaded
    for path in ("/static/app.js", "/static/modules/state.js", "/static/modules/views.js"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["content-type"].startswith("text/javascript"), (
            path,
            res.headers["content-type"],
        )
    css = client.get("/static/style.css")
    assert css.headers["content-type"].startswith("text/css")


def test_health_endpoint_is_public_and_leaks_no_paths(loaded):
    client, ctx = loaded
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["version"] == ctx.app.version
    assert isinstance(body["gateway_installed"], bool)
    assert "data_path" not in res.text and "gateway_path" not in res.text
    assert "api_secret" not in res.text


def test_non_ascii_admin_password_authenticates_instead_of_500(tmp_path, monkeypatch):
    """compare_digest(str) raised TypeError for non-ASCII passwords."""
    password = "رمز-عبور-فارسی-۱۲۳۴۵۶۷۸"
    module, ctx = _build(monkeypatch, tmp_path, password=password)
    with TestClient(module.app) as client:
        assert client.post("/api/auth/login", json={"password": "wrong-passphrase"}).status_code == 401
        res = client.post("/api/auth/login", json={"password": password})
        assert res.status_code == 200, res.text
        assert res.json()["csrf"]
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def test_worker_env_excludes_parent_secrets(monkeypatch):
    """The third-party Node gateway must not inherit Hub secrets."""
    from types import SimpleNamespace

    from hub.workers import worker_env

    monkeypatch.setenv("APP_SECRET", "super-secret-app-value")
    monkeypatch.setenv("ADMIN_PASSWORD", "super-secret-admin-password")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("AGENT_API_KEY", "agent-key-value")
    ctx = SimpleNamespace(
        VENDOR=Path("/vendor/gateway"),
        DATA=Path("/data"),
        setting=lambda key: {"gateway_max_tabs": 3, "max_batch_images": 4}[key],
    )
    acc = {"id": "acc-1", "port": 41005, "api_secret": "worker-secret"}
    env = worker_env(ctx, acc)
    for leaked in ("APP_SECRET", "ADMIN_PASSWORD", "DATABASE_URL", "AGENT_API_KEY"):
        assert leaked not in env, leaked
    assert env["PORT"] == "41005" and env["API_KEY"] == "worker-secret"
    assert env["NODE_PATH"].endswith("node_modules")
    assert env["HEADLESS"] == "false"


def test_unsent_job_is_requeued_not_failed(loaded, monkeypatch):
    """A health blip before submission must not terminally fail a job."""
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Probe", "autostart": False}, headers=h).json()
    job = client.post(
        "/api/generate",
        json={"account_id": acc["id"], "prompt": "requeue me", "aspect_ratio": "1:1"},
        headers=h,
    ).json()
    jid = job["id"]
    from sqlalchemy import update

    with ctx.engine.begin() as conn:
        conn.execute(
            update(ctx.jobs)
            .where(ctx.jobs.c.id == jid)
            .values(status="running", started_at=ctx.now())
        )
    monkeypatch.setattr(ctx, "process_state", lambda account: "stopped")
    monkeypatch.setattr(ctx, "upstream_probe", lambda account: False)
    ctx.execute_job(jid, ctx.query_account(acc["id"]), "requeue me", "1:1", "best", 1)
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"


def _isolate_scheduler(ctx):
    """Stop the live dispatcher so manual supervisor calls are not raced by its thread.

    The background scheduler also supervises workers; on a machine where the gateway is
    really installed it can consume the restart state before the test looks at it.
    """
    import threading

    ctx.stop_scheduler()
    ctx.queue_stop = threading.Event()  # unset: manual calls must proceed


def test_crashed_worker_is_restarted_with_backoff(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    _isolate_scheduler(ctx)
    acc = client.post(
        "/api/accounts",
        json={"name": "Supervised", "autostart": True},
        headers=h,
    ).json()
    started = []
    monkeypatch.setattr(
        ctx, "start_worker_async", lambda account, **kw: started.append(account["id"])
    )
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    ctx.worker_restart_state.clear()
    assert ctx._supervise_workers() == 1
    assert started == [acc["id"]]
    # Immediate second tick is inside the backoff window.
    assert ctx._supervise_workers() == 0
    assert started == [acc["id"]]
    # An operator stop must not be resurrected.
    ctx.worker_restart_state.clear()
    ctx.suppressed_workers.add(acc["id"])
    assert ctx._supervise_workers() == 0
    ctx.suppressed_workers.discard(acc["id"])


def test_sqlite_runs_in_wal_mode(loaded):
    _, ctx = loaded
    with ctx.engine.connect() as conn:
        assert str(conn.exec_driver_sql("PRAGMA journal_mode").scalar()).lower() == "wal"


def test_preview_build_is_byte_reproducible(tmp_path):
    """v0.5 regression: build.py used the locale encoding and rewrote CRLF on Windows."""
    import shutil

    shutil.copytree(ROOT / "frontend", tmp_path / "frontend")
    (tmp_path / "preview").mkdir()
    shutil.copy2(ROOT / "preview" / "build.py", tmp_path / "preview" / "build.py")
    shutil.copy2(ROOT / "preview" / "demo.js", tmp_path / "preview" / "demo.js")
    subprocess.run(
        [sys.executable, str(tmp_path / "preview" / "build.py")],
        check=True,
        capture_output=True,
    )
    rebuilt = (tmp_path / "preview" / "dist" / "index.html").read_bytes()
    assert rebuilt == (ROOT / "preview" / "dist" / "index.html").read_bytes()
    for name in ("app.js", "style.css", "modules/views.js"):
        assert (tmp_path / "preview" / "dist" / "static" / name).read_bytes() == (
            ROOT / "frontend" / name
        ).read_bytes(), name


def test_prepare_gateway_installs_browser_binaries():
    """npm ci alone leaves the worker unable to launch a browser."""
    script = (ROOT / "scripts" / "prepare_gateway.py").read_text(encoding="utf-8")
    assert '"npm"' in script and '"ci"' in script
    assert "playwright" in script and "install" in script
    assert "patchright" in script
    assert "--skip-browsers" in script
    # github.com is blocked on some networks; the codeload fallback keeps install possible.
    assert "codeload.github.com" in script


def test_doctor_console_print_survives_unicode_and_bom():
    """A BOM in PINNED_COMMIT.txt used to crash doctor with UnicodeEncodeError (cp1252)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "doctor_module", ROOT / "scripts" / "doctor.py"
    )
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    doctor.record("ok", "gateway revision", "tarball refs/heads/main \ufeff", "fix")
    assert doctor._console("\ufeffabc").endswith("abc")


class _FakeProc:
    """Minimal stand-in for a subprocess.Popen in worker-lifecycle tests."""

    pid = 424242

    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _settings(**values):
    return lambda key: values.get(key, 0)


def test_idle_reaper_stops_a_worker_but_never_an_operator_stop(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Idle", "autostart": False}, headers=h).json()
    aid = acc["id"]
    monkeypatch.setattr(ctx, "setting", _settings(idle_worker_shutdown_minutes=1))
    proc = _FakeProc()
    ctx.processes[aid] = proc
    ctx.account_activity[aid] = 0.0  # long idle (monotonic clock is large)
    assert ctx._reap_idle_workers() == 1
    assert proc.terminated is True
    assert aid not in ctx.processes
    assert aid in ctx.idle_stopped
    assert aid not in ctx.suppressed_workers  # reaped, not an operator stop


def test_idle_reaper_keeps_busy_account(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Busy", "autostart": False}, headers=h).json()
    aid = acc["id"]
    monkeypatch.setattr(ctx, "setting", _settings(idle_worker_shutdown_minutes=1))
    ctx.processes[aid] = _FakeProc()
    ctx.account_activity[aid] = 0.0
    ctx.active_per_account[aid] = 1
    assert ctx._reap_idle_workers() == 0
    ctx.active_per_account[aid] = 0
    assert aid in ctx.processes


def test_idle_reaper_is_disabled_by_zero(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "AlwaysOn", "autostart": False}, headers=h).json()
    aid = acc["id"]
    monkeypatch.setattr(ctx, "setting", _settings(idle_worker_shutdown_minutes=0))
    ctx.processes[aid] = _FakeProc()
    ctx.account_activity[aid] = 0.0
    assert ctx._reap_idle_workers() == 0
    assert aid in ctx.processes


def test_queued_job_wakes_an_idle_reaped_worker(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "OnDemand", "autostart": False}, headers=h).json()
    aid = acc["id"]
    client.post(
        "/api/generate",
        json={"account_id": aid, "prompt": "wake me up", "aspect_ratio": "1:1"},
        headers=h,
    )
    woken = []
    monkeypatch.setattr(ctx, "wake_worker_async", lambda a, **k: woken.append(a["id"]) or True)
    ctx.idle_stopped.add(aid)
    ctx.suppressed_workers.add(aid)
    assert ctx._wake_for_queue() == 1
    assert woken == [aid]


def test_operator_stopped_account_with_queued_job_is_not_woken(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Stopped", "autostart": False}, headers=h).json()
    aid = acc["id"]
    client.post(
        "/api/generate",
        json={"account_id": aid, "prompt": "leave me queued", "aspect_ratio": "1:1"},
        headers=h,
    )
    ctx.suppressed_workers.add(aid)
    ctx.idle_stopped.discard(aid)
    woken = []
    monkeypatch.setattr(ctx, "wake_worker_async", lambda a, **k: woken.append(a["id"]) or True)
    assert ctx._wake_for_queue() == 0
    assert woken == []


def test_wake_worker_async_clears_markers_and_cools_down(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    acc = client.post("/api/accounts", json={"name": "Wake", "autostart": False}, headers=auth(client)).json()
    aid = acc["id"]
    started = []
    monkeypatch.setattr(ctx, "start_worker_async", lambda a, **k: started.append(a["id"]))
    ctx.idle_stopped.add(aid)
    ctx.suppressed_workers.add(aid)
    ctx.wake_state.clear()
    assert ctx.wake_worker_async(ctx.query_account(aid)) is True
    assert started == [aid]
    assert aid not in ctx.idle_stopped and aid not in ctx.suppressed_workers
    # Immediate second wake is inside the cooldown window.
    assert ctx.wake_worker_async(ctx.query_account(aid)) is False
    assert started == [aid]


def test_idle_shutdown_setting_validates_and_persists(loaded):
    client, _ = loaded
    h = auth(client)
    assert client.patch("/api/control", json={"idle_worker_shutdown_minutes": 2000}, headers=h).status_code == 422
    res = client.patch("/api/control", json={"idle_worker_shutdown_minutes": 15}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["config"]["idle_worker_shutdown_minutes"] == 15
    assert client.get("/api/settings").json()["idle_worker_shutdown_minutes"] == 15


def test_orphan_reclaim_is_scoped_and_skipped_when_port_is_free(loaded, monkeypatch):
    """A hard-killed panel leaves a gateway behind; reclaim must only touch our own."""
    from hub.workers import reclaim_orphan_worker

    client, ctx = loaded
    acc = client.post("/api/accounts", json={"name": "Orphan", "autostart": False}, headers=auth(client)).json()
    monkeypatch.setattr(ctx, "port_available", lambda port: True)
    assert reclaim_orphan_worker(ctx, ctx.query_account(acc["id"])) is False


def test_wake_backoff_state_escalates(loaded, monkeypatch):
    client, ctx = loaded
    _isolate_scheduler(ctx)
    acc = client.post("/api/accounts", json={"name": "Backoff", "autostart": False}, headers=auth(client)).json()
    aid = acc["id"]
    ctx.wake_state.clear()
    monkeypatch.setattr(ctx, "start_worker_async", lambda a, **k: None)
    assert ctx.wake_worker_async(ctx.query_account(aid)) is True
    first = ctx.wake_state[aid]["next"]
    assert ctx.wake_worker_async(ctx.query_account(aid)) is False  # inside cooldown
    ctx.wake_state[aid]["next"] = 0.0
    assert ctx.wake_worker_async(ctx.query_account(aid)) is True
    assert ctx.wake_state[aid]["attempts"] == 2
    assert ctx.wake_state[aid]["next"] > first


def test_ram_guard_refuses_a_new_worker_instead_of_oom(loaded, monkeypatch):
    """With 10 accounts a single OOM kills everything; the guard must refuse the start."""
    import psutil

    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "RamGuard", "autostart": False}, headers=h).json()
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    monkeypatch.setattr(ctx, "ensure_worker_copy", lambda account: "never reached")
    monkeypatch.setattr(
        ctx, "setting", _settings(min_free_memory_mb=600, max_running_accounts=10)
    )
    fake = type("VM", (), {"available": 100 * 1024 * 1024})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: fake)
    with pytest.raises(RuntimeError) as excinfo:
        ctx.start_worker(ctx.query_account(acc["id"]))
    assert "Not enough free RAM" in str(excinfo.value)


def test_global_inflight_cap_is_32_and_validated(loaded):
    client, _ = loaded
    h = auth(client)
    assert client.patch("/api/control", json={"global_max_inflight": 33}, headers=h).status_code == 422
    res = client.patch("/api/control", json={"global_max_inflight": 10}, headers=h)
    assert res.status_code == 200
    assert res.json()["config"]["global_max_inflight"] == 10


def test_job_pool_can_hold_the_maximum_global_cap(loaded):
    _, ctx = loaded
    assert ctx.pool._max_workers >= 32
    assert ctx.health_pool._max_workers >= 10


def test_auto_login_roundtrip_never_leaks_the_password(loaded):
    """Optional per-account credentials for the gateway's own re-login."""
    from hub.workers import worker_env

    client, ctx = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "AutoLogin", "autostart": False}, headers=h
    ).json()
    aid = acc["id"]
    res = client.put(
        f"/api/accounts/{aid}/auto-login",
        headers=h,
        json={
            "email": "bot@example.com",
            "password": "correct horse battery",
            "totp_secret": "JBSW Y3DP EHPK 3PXP",
        },
    )
    assert res.status_code == 200, res.text
    assert "correct horse" not in res.text
    status = client.get(f"/api/accounts/{aid}/auto-login")
    assert status.json() == {
        "configured": True,
        "email": "bot@example.com",
        "has_totp": True,
    }
    assert "password" not in status.text
    assert client.get("/api/accounts").json()[0]["auto_login_configured"] is True

    env = worker_env(ctx, ctx.query_account(aid))
    assert env["CHATGPT_EMAIL"] == "bot@example.com"
    assert env["CHATGPT_PASSWORD"] == "correct horse battery"
    assert env["CHATGPT_TOTP_SECRET"] == "JBSWY3DPEHPK3PXP"
    # The Hub's own secrets still never reach the worker.
    assert "APP_SECRET" not in env and "DATABASE_URL" not in env


def test_auto_login_validates_and_clears(loaded):
    import os

    client, ctx = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "Validate", "autostart": False}, headers=h
    ).json()
    aid = acc["id"]
    assert client.put(
        f"/api/accounts/{aid}/auto-login",
        headers=h,
        json={"email": "not-an-email", "password": "x"},
    ).status_code == 422
    assert client.put(
        f"/api/accounts/{aid}/auto-login",
        headers=h,
        json={"email": "a@b.co", "password": "x", "totp_secret": "!!!"},
    ).status_code == 422
    assert client.put(
        f"/api/accounts/{aid}/auto-login", headers=h, json={"email": "a@b.co", "password": ""}
    ).status_code == 422
    # Session + CSRF are required.
    assert client.put(
        f"/api/accounts/{aid}/auto-login", json={"email": "a@b.co", "password": "x"}
    ).status_code == 403
    assert client.get("/api/accounts/nope/auto-login").status_code == 404

    assert client.put(
        f"/api/accounts/{aid}/auto-login",
        headers=h,
        json={"email": "a@b.co", "password": "secret-value"},
    ).status_code == 200
    path = ctx.DATA / "accounts" / aid / "auto-login.json"
    assert path.is_file()
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    cleared = client.delete(f"/api/accounts/{aid}/auto-login", headers=h)
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert not path.exists()


def test_icons_are_bundled_locally_and_complete():
    """No CDN, no Unicode glyph fallbacks: one Lucide sprite, every reference resolves."""
    import re

    sprite = ROOT / "frontend" / "icons" / "sprite.svg"
    assert sprite.is_file(), "frontend/icons/sprite.svg is missing"
    assert (ROOT / "frontend" / "icons" / "LICENSE.txt").is_file()
    ids = set(re.findall(r'id="lc-([a-z0-9-]+)"', sprite.read_text(encoding="utf-8")))
    assert len(ids) >= 25, f"expected the full icon set, found {len(ids)}"

    sources = [ROOT / "frontend" / "index.html"] + sorted(
        (ROOT / "frontend").glob("modules/*.js")
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert "cdn.jsdelivr" not in text and "lucide-icons/lucide@main" not in text
    assert "icon-fallback" not in text and "nav-image" not in text
    used = set(re.findall(r"sprite\.svg#lc-([a-z0-9-]+)", text))
    occurrences = len(re.findall(r"sprite\.svg#lc-[a-z0-9-]+", text))
    assert len(used) >= 15, f"expected icons across the UI, found {len(used)}"
    assert occurrences >= 30, f"expected icons on most controls, found {occurrences}"
    missing = sorted(used - ids)
    assert not missing, f"icons referenced but absent from the sprite: {missing}"
    # Nav must carry real icons, not text glyphs.
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for view in ("overview", "analytics", "operations", "accounts", "studio", "jobs", "gallery", "settings"):
        assert f'data-view="{view}"' in html
    assert html.count("sprite.svg#lc-") >= 8


def test_ui_bundle_contract():
    """Bundle A+B: skip link, favicon, labels, chart empty state, jobs toolbar, toasts."""
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
    views = (ROOT / "frontend" / "modules" / "views.js").read_text(encoding="utf-8")

    # Bundle A: accessibility, favicon, dialog validation, naming, chart empty state
    assert 'class="skip-link" href="#mainContent"' in html
    assert 'id="mainContent"' in html and 'tabindex="-1"' in html
    assert 'rel="icon"' in html and "favicon.svg" in html
    assert 'name="theme-color"' in html
    assert 'id="closeLogs" aria-label="Close logs"' in html
    assert 'id="autoLoginForm" novalidate' in html
    assert "<h1>Analytics<span" in html and "Performance" not in html
    assert "Performance" not in (
        ROOT / "frontend" / "modules" / "controller.js"
    ).read_text(encoding="utf-8")
    assert "chart-empty" in views and "chartHasData" in views
    assert "chart-y" in css and ".chart-body" in css and ".skip-link" in css

    # Bundle B: jobs toolbar, responsive cards, account menu, sticky CTA, skeletons, toasts
    assert 'id="jobFilters"' in html and 'id="jobSearch"' in html
    assert 'class="jobs-table"' in html and 'id="jobCount"' in html
    assert "data-label=" in views and "filterJobs" in views
    assert "jobs-table td::before" in css
    assert "action-menu" in views and ".action-menu-list" in css
    assert 'class="generate-bar"' in html and ".generate-bar" in css
    assert "skeleton-card" in html and ".skeleton-card" in css
    assert 'id="toastRegion"' in html
    assert 'id="toast"' not in html  # the old single-toast node is gone
    assert ".toast-region" in css and ".toast-close" in css
    assert "uiIcon(" in (
        ROOT / "frontend" / "modules" / "dom.js"
    ).read_text(encoding="utf-8")


def test_icon_sprite_regenerator_reports_success():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_icons.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "sprite OK" in result.stdout


def test_login_page_ships_the_resilient_client_flow(loaded):
    """The remote login page must survive a slow worker, a busy browser and lease expiry."""
    client, _ = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "LoginFlow", "autostart": False}, headers=h
    ).json()
    res = client.get(f"/accounts/{acc['id']}/login")
    assert res.status_code == 200, res.text
    page = res.text
    for marker in (
        "bootstrap()",              # retry loop while the browser wakes
        "MAX_POLL_MS",              # stops before the gateway's 10-minute lease
        "naturalWidth",             # clicks scale by the real screenshot size
        "Saving the session",       # save state
        "Retrying automatically",   # busy-job / transient failures
        "Panel session expired",    # CSRF/session rotation
        "Open ChatGPT",             # explicit recovery action
        "x-csrf-token",             # every action carries CSRF
        'id="marker"',              # visible click feedback (there is no live cursor)
        "toggleDirect",             # direct keyboard capture
        "wheelScroll",              # wheel -> PageUp/PageDown
        "Type directly: on",        # direct typing is the default now
        "addEventListener('paste'",  # paste a password from a manager
        "follows the newest window",  # OAuth popup guidance
    ):
        assert marker in page, marker


def test_login_click_accepts_real_viewport_sizes():
    from hub.schemas import LoginClick

    assert LoginClick(x=1920, y=1080).x == 1920
    with pytest.raises(Exception):
        LoginClick(x=-1, y=10)
    with pytest.raises(Exception):
        LoginClick(x=10, y=5000)


def test_settings_reports_display_mode(loaded):
    client, _ = loaded
    h = auth(client)
    data = client.get("/api/settings", headers=h).json()
    assert data["display_mode"] in {"desktop", "x11", "xvfb", "missing"}


def test_metrics_parser_extracts_login_blockers():
    from hub.workers import _parse_metrics

    unverified = (
        '# HELP gpt_web_gateway_session_valid x\n'
        "gpt_web_gateway_session_valid 0\n"
        'gpt_web_gateway_login_failures_total{blocker="mfa_required"} 2\n'
        'gpt_web_gateway_login_failures_total{blocker="cloudflare_challenge"} 0\n'
    )
    parsed = _parse_metrics(unverified)
    assert parsed == {"state": "unverified", "blocker": "mfa_required"}
    connected = "gpt_web_gateway_session_valid 1\n"
    assert _parse_metrics(connected) == {"state": "connected", "blocker": None}


def test_login_init_reports_busy_browser_instead_of_hanging(loaded, monkeypatch):
    """A running generation holds the gateway's exclusive lock; init must fail clearly."""
    import asyncio

    import httpx

    from hub import login as login_module

    client, ctx = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "BusyInit", "autostart": False}, headers=h
    ).json()
    account = ctx.query_account(acc["id"])
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    monkeypatch.setattr(ctx, "process_state", lambda a: "running")
    monkeypatch.setattr(ctx, "upstream_probe", lambda a: True)

    class TimedOutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *args, **kwargs):
            raise httpx.ReadTimeout("busy")

    monkeypatch.setattr(login_module.httpx, "AsyncClient", TimedOutClient)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(login_module.upstream_login_call(ctx, acc["id"], "init"))
    assert "busy with a running generation job" in str(excinfo.value)



def test_popup_follow_patch_is_applied_once_and_skips_unknown_upstream(tmp_path):
    """Google/Apple sign-in opens a popup; the worker's login route must follow it."""
    from hub.workers import _patch_popup_follow

    worker = tmp_path / "gateway"
    routes = worker / "src" / "routes"
    routes.mkdir(parents=True)
    upstream = (
        "async function getLoginPage() {\n"
        "  if (loginPage && !loginPage.isClosed()) return loginPage;\n"
        "  const ctx = await getContext();\n"
        "}\n"
    )
    target = routes / "login.js"
    target.write_text(upstream, encoding="utf-8")
    assert _patch_popup_follow(worker) is True
    patched = target.read_text(encoding="utf-8")
    assert "ninja:popup-follow" in patched
    assert "context.pages()" in patched
    assert _patch_popup_follow(worker) is False  # idempotent
    assert patched.count("ninja:popup-follow") == 1

    changed = routes / "login-changed.js"
    changed.write_text("function getLoginPage() { return null; }\n", encoding="utf-8")
    routes.joinpath("login.js").write_text("// unrelated upstream\n", encoding="utf-8")
    assert _patch_popup_follow(worker) is False
    assert routes.joinpath("login.js").read_text(encoding="utf-8") == "// unrelated upstream\n"


def test_account_status_exposes_worker_display_for_vnc(loaded, monkeypatch):
    import psutil

    from hub.workers import worker_display

    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Display", "autostart": False}, headers=h).json()

    class FakeProc:
        def poll(self):
            return None

        pid = 4242

    ctx.processes[acc["id"]] = FakeProc()
    monkeypatch.setattr(
        psutil, "Process", lambda pid: type("P", (), {"environ": lambda self: {"DISPLAY": ":99"}})()
    )
    assert worker_display(ctx, ctx.query_account(acc["id"])) == ":99"
    monkeypatch.setattr(
        psutil, "Process", lambda pid: type("P", (), {"environ": lambda self: {}})()
    )
    assert worker_display(ctx, ctx.query_account(acc["id"])) is None
    ctx.processes.pop(acc["id"], None)  # do not let the fake process reach shutdown



def test_project_url_is_stored_validated_and_passed_to_the_worker(loaded):
    """Every new chat for an account must open inside the configured ChatGPT project."""
    from hub.workers import worker_env

    client, ctx = loaded
    h = auth(client)
    project = "https://chatgpt.com/g/g-p-abc123-ninja-image-hub/project"
    acc = client.post(
        "/api/accounts",
        json={"name": "Project", "autostart": False, "project_url": project},
        headers=h,
    )
    assert acc.status_code == 201, acc.text
    aid = acc.json()["id"]
    assert acc.json()["project_url"] == project
    assert client.get("/api/accounts").json()[0]["project_url"] == project

    env = worker_env(ctx, ctx.query_account(aid))
    assert env["CHATGPT_PROJECT_URL"] == project

    # Editing stores the new URL, and clearing it goes back to the normal chat list.
    assert client.patch(
        f"/api/accounts/{aid}", json={"name": "Project"}, headers=h
    ).json()["project_url"] == ""
    assert client.patch(
        f"/api/accounts/{aid}",
        json={"name": "Project", "project_url": project},
        headers=h,
    ).json()["project_url"] == project

    bad = client.post(
        "/api/accounts",
        json={"name": "Bad project", "project_url": "https://example.com/not-a-project"},
        headers=h,
    )
    assert bad.status_code == 422


def test_project_url_patch_is_idempotent_and_skips_unknown_upstream(tmp_path):
    from hub.workers import _patch_project_url

    worker = tmp_path / "gateway"
    (worker / "src").mkdir(parents=True)
    chatgpt = worker / "src" / "chatgpt.js"
    chatgpt.write_text(
        "const CHATGPT_URL = 'https://chatgpt.com';\n// rest of upstream\n",
        encoding="utf-8",
    )
    assert _patch_project_url(worker) is True
    patched = chatgpt.read_text(encoding="utf-8")
    assert "ninja:project-url" in patched
    assert "process.env.CHATGPT_PROJECT_URL" in patched
    assert _patch_project_url(worker) is False  # idempotent

    chatgpt.write_text("// upstream changed\n", encoding="utf-8")
    assert _patch_project_url(worker) is False
    assert chatgpt.read_text(encoding="utf-8") == "// upstream changed\n"



def test_login_diagnostics_patch_is_idempotent_and_upgrades_old_copies(tmp_path):
    """/login/url and /login/pages are how project routing is verified."""
    from hub.workers import _patch_login_diagnostics

    worker = tmp_path / "gateway"
    routes = worker / "src" / "routes"
    routes.mkdir(parents=True)
    login_js = routes / "login.js"
    login_js.write_text(
        "const express = require('express');\nmodule.exports = router;\n",
        encoding="utf-8",
    )
    assert _patch_login_diagnostics(worker) is True
    patched = login_js.read_text(encoding="utf-8")
    assert "/login/url" in patched and "/login/pages" in patched
    assert patched.count("module.exports = router;") == 1
    assert _patch_login_diagnostics(worker) is False  # idempotent

    # A copy written by the first release called enterManualLogin() on the page variable.
    login_js.write_text(
        "// ninja:pages-routes\n"
        "router.get('/login/url', async (req, res) => {\n"
        "  try {\n"
        "    const p = await enterManualLogin({ acquire: false });\n"
        "    res.json({ url: p.url(), title: await p.title().catch(() => '') });\n"
        "  } catch (err) { res.status(500).json({ error: err.message }); }\n"
        "});\n"
        "module.exports = router;\n",
        encoding="utf-8",
    )
    assert _patch_login_diagnostics(worker) is True
    upgraded = login_js.read_text(encoding="utf-8")
    assert "const page = await getLoginPage();" in upgraded
    assert "const p = await enterManualLogin" not in upgraded

    # Unknown upstream: never touch the file.
    login_js.write_text("// totally different\n", encoding="utf-8")
    assert _patch_login_diagnostics(worker) is False


def test_worker_command_gives_every_linux_worker_its_own_xvfb():
    """A headed browser on a server needs a virtual display — one per worker, never shared."""
    from hub.workers import display_mode, worker_command

    no_tools = lambda name: None  # noqa: E731
    xvfb = lambda name: "/usr/bin/xvfb-run"  # noqa: E731

    assert display_mode("win32", which=no_tools, env={}) == "desktop"
    assert worker_command("win32", which=no_tools, env={}) == ["node", "src/server.js"]
    assert display_mode("linux", which=xvfb, env={}) == "xvfb"
    command = worker_command("linux", which=xvfb, env={})
    assert command[:2] == ["xvfb-run", "-a"]
    assert command[-2:] == ["node", "src/server.js"]
    # A real X11 display (VNC/desktop) is used as-is.
    assert display_mode("linux", which=xvfb, env={"DISPLAY": ":99"}) == "x11"
    assert worker_command("linux", which=xvfb, env={"DISPLAY": ":99"}) == [
        "node",
        "src/server.js",
    ]
    # No display and no Xvfb must be reported as a hard failure, not a silent headless run.
    assert display_mode("linux", which=no_tools, env={}) == "missing"


def test_linux_deploy_assets_exist_and_are_consistent():
    service = (ROOT / "deploy" / "ninja-image-hub.service").read_text(encoding="utf-8")
    installer = (ROOT / "deploy" / "install_linux.sh").read_text(encoding="utf-8")
    exec_line = next(
        line for line in service.splitlines() if line.startswith("ExecStart=")
    )
    assert "--workers 1" in exec_line and "--reload" not in exec_line
    assert "EnvironmentFile=" in service and "ninja-image-hub" in service
    assert "xvfb" in installer
    assert "google-chrome" in installer
    assert "prepare_gateway.py" in installer
    assert "systemctl" in installer
    doc = (ROOT / "docs" / "DEPLOY_LINUX_FA.md").read_text(encoding="utf-8")
    assert "Open ChatGPT" in doc and "Save Session" in doc


def test_doctor_reports_actionable_status(tmp_path):
    """Run a copy of doctor.py in an empty tree so the real .env/vendor cannot hide FAILs."""
    import shutil

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "doctor.py", repo / "scripts" / "doctor.py")
    env = dict(os.environ)
    env.pop("APP_SECRET", None)
    env.pop("ADMIN_PASSWORD", None)
    env["DATA_DIR"] = str(tmp_path / "doctor-data")
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "doctor.py"), "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo),
    )
    # --json must be pipeable: the whole stdout is one JSON document.
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    names = {item["name"] for item in payload["failures"]}
    assert "gateway" in names or "env file" in names
    assert result.returncode == 1


def test_non_ascii_csrf_header_is_403_not_500(loaded):
    """A non-ASCII header value used to raise TypeError inside compare_digest -> 500."""
    client, _ = loaded
    h = auth(client)
    raw = h["x-csrf-token"].encode("ascii")[:-1] + b"\xe9"
    res = client.post(
        "/api/accounts",
        json={"name": "non-ascii csrf"},
        headers={"x-csrf-token": raw},
    )
    assert res.status_code == 403, res.text


def test_lone_surrogate_password_is_401_not_500(loaded):
    """A lone surrogate JSON escape used to raise UnicodeEncodeError -> 500."""
    client, _ = loaded
    res = client.post(
        "/api/auth/login",
        content=b'{"password":"\\ud800"}',
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 401, res.text


def test_sqlalchemy_pysqlite_url_still_gets_wal(tmp_path, monkeypatch):
    url = "sqlite+pysqlite:///" + str(tmp_path / "explicit.db").replace("\\", "/")
    module, ctx = _build(monkeypatch, tmp_path, db_url=url)
    with TestClient(module.app):
        with ctx.engine.connect() as conn:
            mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(mode).lower() == "wal"
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def test_in_memory_sqlite_is_usable(tmp_path, monkeypatch):
    module, ctx = _build(monkeypatch, tmp_path, db_url="sqlite:///:memory:")
    with TestClient(module.app) as client:
        res = client.get("/api/health")
        assert res.status_code == 200 and res.json()["ok"] is True
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def test_supervisor_gives_up_after_max_attempts(loaded, monkeypatch):
    from hub.scheduler import MAX_RESTART_ATTEMPTS

    client, ctx = loaded
    h = auth(client)
    _isolate_scheduler(ctx)
    acc = client.post(
        "/api/accounts", json={"name": "Broken", "autostart": True}, headers=h
    ).json()
    started = []
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    monkeypatch.setattr(
        ctx, "start_worker_async", lambda a, **k: started.append(a["id"])
    )
    ctx.worker_restart_state[acc["id"]] = {
        "attempts": MAX_RESTART_ATTEMPTS,
        "next": 0.0,
    }
    assert ctx._supervise_workers() == 0
    assert started == []
    assert ctx.worker_restart_state[acc["id"]]["exhausted"] is True
    # A manual Start clears the state and re-arms supervision.
    ctx.worker_restart_state.pop(acc["id"])
    assert ctx._supervise_workers() == 1
    assert started == [acc["id"]]


def test_automatic_start_never_overrides_operator_stop(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "Stopped", "autostart": True}, headers=h
    ).json()
    ctx.suppressed_workers.add(acc["id"])

    def forbidden(*args, **kwargs):
        raise AssertionError("automatic start must not launch a suppressed worker")

    monkeypatch.setattr(ctx, "ensure_worker_copy", forbidden)
    ctx.start_worker(ctx.query_account(acc["id"]), manual=False)  # returns silently
    ctx.suppressed_workers.discard(acc["id"])
