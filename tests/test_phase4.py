"""Phase 4 tests: reconciliation, maintenance, disk guard, backup, watch, capacity math."""
import importlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "example-passphrase-123456789")
    monkeypatch.setenv("APP_SECRET", "a" * 64)
    monkeypatch.setenv("AGENT_API_KEY", "test-agent-key-98765")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "hub.db"))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    ctx = module.app.state.hub
    ctx.app = module.app
    # Hermetic: never treat a developer machine's vendor/ checkout as installed.
    monkeypatch.setattr(ctx, "runtime_installed", lambda: False)
    with TestClient(module.app) as client:
        yield client, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def auth(client, password="example-passphrase-123456789"):
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    return {"x-csrf-token": response.json()["csrf"]}


def _load_script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_module", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(**values):
    return lambda key: values.get(key, 0)


# ------------------------------------------------------------------ reconciliation
def test_unknown_job_can_be_reconciled_once(loaded):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Reconcile", "autostart": False}, headers=h).json()
    jid = client.post(
        "/api/generate",
        json={"account_id": acc["id"], "prompt": "uncertain outcome", "aspect_ratio": "1:1"},
        headers=h,
    ).json()["id"]
    from sqlalchemy import update

    with ctx.engine.begin() as conn:
        conn.execute(
            update(ctx.jobs)
            .where(ctx.jobs.c.id == jid)
            .values(status="unknown", error="response lost", finished_at=ctx.now())
        )

    assert client.get("/api/health").json()["unresolved_unknown"] == 1

    # No CSRF -> refused.
    assert (
        client.post(f"/api/jobs/{jid}/resolve", json={"resolution": "not_delivered"}).status_code
        == 403
    )
    # Invalid resolution -> 422.
    assert (
        client.post(
            f"/api/jobs/{jid}/resolve", json={"resolution": "maybe"}, headers=h
        ).status_code
        == 422
    )
    # Wrong status -> 409.
    other = client.post(
        "/api/generate",
        json={"account_id": acc["id"], "prompt": "still queued", "aspect_ratio": "1:1"},
        headers=h,
    ).json()["id"]
    assert (
        client.post(
            f"/api/jobs/{other}/resolve", json={"resolution": "abandoned"}, headers=h
        ).status_code
        == 409
    )

    result = client.post(
        f"/api/jobs/{jid}/resolve",
        json={"resolution": "delivered_manually", "note": "found in chat history"},
        headers=h,
    )
    assert result.status_code == 200, result.text
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "unknown"  # never rewritten
    assert job["resolution"] == "delivered_manually"
    assert job["resolved_at"]
    assert job["resolution_note"] == "found in chat history"
    assert client.get("/api/health").json()["unresolved_unknown"] == 0
    # Second resolution is refused.
    assert (
        client.post(
            f"/api/jobs/{jid}/resolve", json={"resolution": "abandoned"}, headers=h
        ).status_code
        == 409
    )
    # Audited without the operator's note.
    audit = client.get("/api/control/audit?limit=20").json()
    entries = [a for a in audit if a["action"] == "job.resolve"]
    assert entries and "found in chat history" not in entries[0]["detail"]
    assert jid in entries[0]["detail"]


# ------------------------------------------------------------------ maintenance
def test_maintenance_prunes_only_derived_data(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Maintenance", "autostart": False}, headers=h).json()
    aid = acc["id"]
    data = ctx.DATA
    old = time.time() - 3 * 86400
    fresh = time.time()

    previews = data / "previews"
    previews.mkdir(exist_ok=True)
    stale_preview = previews / "stale-1-2.webp"
    fresh_preview = previews / "fresh-1-2.webp"
    stale_preview.write_bytes(b"webp")
    fresh_preview.write_bytes(b"webp")

    exports = data / "exports"
    exports.mkdir(exist_ok=True)
    stale_export = exports / "job-old.zip"
    stale_export.write_bytes(b"zip")

    gallery = data / "gallery"
    gallery.mkdir(exist_ok=True)
    original = gallery / "00000000-0000-0000-0000-000000000001-1.png"
    original.write_bytes(b"original-bytes")
    incoming = gallery / ".incoming-leftover"
    incoming.write_bytes(b"partial")

    worker_dir = data / "accounts" / aid
    (worker_dir / "gateway" / "auth").mkdir(parents=True, exist_ok=True)
    auto_login = worker_dir / "auto-login.json"
    auto_login.write_bytes(b'{"email": "x"}')
    session = worker_dir / "gateway" / "auth" / "session.json"
    session.write_bytes(b'{"cookies": []}')
    stopped_log = worker_dir / "worker.log"
    stopped_log.write_bytes(b"x" * (2 * 1024 * 1024))

    for path in (stale_preview, stale_export, incoming):
        os.utime(path, (old, old))

    monkeypatch.setattr(
        ctx,
        "setting",
        _settings(
            preview_retention_days=1,
            export_retention_hours=1,
            worker_log_max_mb=1,
            min_free_disk_mb=0,
        ),
    )
    ctx.last_maintenance = 0
    result = ctx._run_maintenance()
    assert result["previews_removed"] == 1
    assert result["exports_removed"] == 1
    assert result["incoming_removed"] == 1
    assert result["logs_rotated"] == 1

    assert not stale_preview.exists()
    assert fresh_preview.exists()
    assert not stale_export.exists()
    assert original.exists()  # originals are never pruned
    assert auto_login.exists() and session.exists()
    assert (worker_dir / "worker.log.1").exists()
    db_file = Path(ctx.DB_URL.replace("sqlite:///", ""))
    assert db_file.exists()  # the database itself is never touched

    audit = client.get("/api/control/audit?limit=20").json()
    assert any(a["action"] == "maintenance.cleanup" for a in audit)


def test_maintenance_never_rotates_a_live_worker_log(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Live", "autostart": False}, headers=h).json()
    aid = acc["id"]
    worker_dir = ctx.DATA / "accounts" / aid
    worker_dir.mkdir(parents=True, exist_ok=True)
    log = worker_dir / "worker.log"
    log.write_bytes(b"x" * (2 * 1024 * 1024))
    ctx.processes[aid] = _FakeProc()
    monkeypatch.setattr(
        ctx,
        "setting",
        _settings(preview_retention_days=0, export_retention_hours=0, worker_log_max_mb=1, min_free_disk_mb=0),
    )
    ctx.last_maintenance = 0
    assert ctx._run_maintenance()["logs_rotated"] == 0
    assert log.exists()
    ctx.processes.pop(aid, None)


def test_dispatch_stops_when_disk_is_low(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Disk", "autostart": False}, headers=h).json()
    client.post(
        "/api/generate",
        json={"account_id": acc["id"], "prompt": "do not dispatch", "aspect_ratio": "1:1"},
        headers=h,
    )
    monkeypatch.setattr(ctx, "disk_low", True, raising=False)
    assert ctx._dispatch_available() == 0
    assert client.get(f"/api/jobs?limit=5").json()[0]["status"] == "queued"


def test_health_reports_disk_and_unresolved(loaded, monkeypatch):
    client, ctx = loaded
    import hub.system as system

    monkeypatch.setattr(
        system.shutil,
        "disk_usage",
        lambda path: type("Usage", (), {"total": 10**12, "used": 0, "free": 100 * 1024 * 1024})(),
    )
    monkeypatch.setattr(ctx, "setting", _settings(min_free_disk_mb=500))
    health = client.get("/api/health").json()
    assert health["version"].startswith("0.7")
    assert health["disk_free_mb"] == 100
    assert health["disk_low"] is True
    assert health["unresolved_unknown"] == 0


# ------------------------------------------------------------------ control settings
def test_retention_settings_validate_and_persist(loaded):
    client, _ = loaded
    h = auth(client)
    assert (
        client.patch(
            "/api/control", json={"preview_retention_days": 4000}, headers=h
        ).status_code
        == 422
    )
    result = client.patch(
        "/api/control",
        json={
            "preview_retention_days": 14,
            "export_retention_hours": 6,
            "worker_log_max_mb": 10,
            "min_free_disk_mb": 256,
        },
        headers=h,
    )
    assert result.status_code == 200, result.text
    config = result.json()["config"]
    assert config["preview_retention_days"] == 14
    assert config["export_retention_hours"] == 6
    assert config["worker_log_max_mb"] == 10
    assert config["min_free_disk_mb"] == 256
    settings = client.get("/api/settings").json()
    assert settings["preview_retention_days"] == 14
    assert settings["disk_free_mb"] is not None


# ------------------------------------------------------------------ backup script
def test_backup_create_verify_and_restore(tmp_path):
    data = tmp_path / "data"
    (data / "gallery").mkdir(parents=True)
    (data / "accounts" / "abc").mkdir(parents=True)
    (data / "gallery" / "00000000-0000-0000-0000-000000000001-1.png").write_bytes(b"png-bytes")
    (data / "accounts" / "abc" / "auto-login.json").write_bytes(b'{"email":"x"}')
    (data / "accounts" / "abc" / "gateway" / "auth").mkdir(parents=True)
    (data / "accounts" / "abc" / "gateway" / "auth" / "session.json").write_bytes(b"{}")
    connection = sqlite3.connect(str(data / "hub.db"))
    connection.execute("CREATE TABLE t (id TEXT)")
    connection.execute("INSERT INTO t VALUES ('x')")
    connection.commit()
    connection.close()

    backup_dir = tmp_path / "backup"
    env = dict(os.environ, DATA_DIR=str(data), DATABASE_URL="")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup.py"), "create", "--dest", str(backup_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert (backup_dir / "hub.db").is_file()
    assert (backup_dir / "backup.json").is_file()

    verify = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup.py"), "verify", "--path", str(backup_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "backup OK" in verify.stdout

    # Tampering is detected.
    (backup_dir / "hub.db").write_bytes(b"corrupt")
    broken = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup.py"), "verify", "--path", str(backup_dir)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert broken.returncode == 1
    assert "CORRUPT" in broken.stdout

    restore_target = tmp_path / "restored"
    restore = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backup.py"),
            "restore",
            "--path",
            str(backup_dir),
            "--target",
            str(restore_target),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert restore.returncode == 0, restore.stderr
    assert (restore_target / "gallery" / "00000000-0000-0000-0000-000000000001-1.png").is_file()


# ------------------------------------------------------------------ watch script
def test_watch_alert_evaluation():
    watch = _load_script("watch")
    clean = {
        "accounts": 2,
        "queued": 0,
        "running_workers": 2,
        "disk_free_mb": 9000,
        "disk_low": False,
        "unresolved_unknown": 0,
    }
    assert watch.evaluate(clean) == []
    assert watch.evaluate(None)[0]["key"] == "unreachable"

    low_disk = dict(clean, disk_free_mb=100)
    assert [a["key"] for a in watch.evaluate(low_disk, None, {"disk_min_mb": 500})] == ["disk_low"]

    unknown = dict(clean, unresolved_unknown=3)
    assert [a["key"] for a in watch.evaluate(unknown)] == ["unresolved_unknown"]

    stalled = dict(clean, queued=4, running_workers=0)
    assert [a["key"] for a in watch.evaluate(stalled)] == ["workers_down"]

    aged = dict(clean, queued=1, running_workers=1)
    queue = {"oldest_queued_age_min": 40}
    assert [a["key"] for a in watch.evaluate(aged, queue, {"queued_age_min": 15})] == ["queue_age"]


# ------------------------------------------------------------------ capacity math
def test_capacity_report_helpers():
    capacity = _load_script("capacity_report")
    assert capacity.percentile([], 0.5) is None
    assert capacity.percentile([10, 20, 30], 0.5) == 20
    summary = capacity.summarize(
        [
            {"status": "completed", "image_count": 2},
            {"status": "failed", "image_count": 0},
            {"status": "unknown", "image_count": 1},
        ]
    )
    assert summary["jobs"] == 3
    assert summary["settled"] == 3
    assert summary["images"] == 3
    assert summary["counts"]["failed"] == 1


# ------------------------------------------------------------------ UI contract
def test_phase4_ui_contract():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    views = (ROOT / "frontend" / "modules" / "views.js").read_text(encoding="utf-8")
    accounts = (ROOT / "frontend" / "modules" / "accounts.js").read_text(encoding="utf-8")
    for name in (
        "preview_retention_days",
        "export_retention_hours",
        "worker_log_max_mb",
        "min_free_disk_mb",
    ):
        assert f'name="{name}"' in html
    assert 'id="resolveDialog"' in html and 'name="resolution"' in html
    assert "resolve-job" in views and "resolution" in views
    assert "initResolveDialog" in accounts
    assert "queueWaitP50" in html and "unresolvedUnknown" in html
    assert "Disk free" in views


# ------------------------------------------------------------------ shared fake process
class _FakeProc:
    def poll(self):
        return None
