"""Start/stop guard paths with fake processes (no browser ever launched)."""
import sys
from types import SimpleNamespace

import pytest

from hub import worker_process


class _FakePopen:
    pid = 424242

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def _account(ctx, client, h, name="Guards"):
    created = client.post(
        "/api/accounts", json={"name": name, "autostart": False}, headers=h
    ).json()
    return ctx.query_account(created["id"])


def _auth(client):
    return {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}


def _prepared(ctx, monkeypatch, tmp_path, *, cap=4, memory=0):
    monkeypatch.setattr(ctx, "ensure_worker_copy", lambda acc: tmp_path)
    monkeypatch.setattr(worker_process, "display_mode", lambda: "desktop")
    monkeypatch.setattr(worker_process, "worker_env", lambda ctx, acc: {})
    monkeypatch.setattr(worker_process, "worker_command", lambda: ["node", "src/server.js"])
    monkeypatch.setattr(worker_process, "touch_account", lambda ctx, aid: None)
    monkeypatch.setattr(
        ctx,
        "setting",
        lambda key: cap
        if key == "max_running_accounts"
        else memory
        if key == "min_free_memory_mb"
        else 4,
    )
    monkeypatch.setattr(ctx, "port_available", lambda port: True)


def test_start_worker_success_sets_process_and_log_handles(loaded, monkeypatch, tmp_path):
    client, ctx = loaded
    account = _account(ctx, client, _auth(client))
    _prepared(ctx, monkeypatch, tmp_path)
    monkeypatch.setattr(worker_process.subprocess, "Popen", _FakePopen)
    worker_process.start_worker(ctx, account, manual=True)
    assert account["id"] in ctx.processes
    assert account["id"] in ctx.log_handles
    ctx.stop_worker(account["id"])  # clean up the opened log handle


def test_start_worker_respects_operator_stop_and_existing_process(loaded, monkeypatch, tmp_path):
    client, ctx = loaded
    account = _account(ctx, client, _auth(client), "Suppressed")
    _prepared(ctx, monkeypatch, tmp_path)
    monkeypatch.setattr(worker_process.subprocess, "Popen", _FakePopen)
    ctx.suppressed_workers.add(account["id"])
    worker_process.start_worker(ctx, account, manual=False)
    assert account["id"] not in ctx.processes  # automatic start must not override Stop
    ctx.suppressed_workers.discard(account["id"])
    ctx.processes[account["id"]] = _FakePopen()
    worker_process.start_worker(ctx, account, manual=True)
    assert isinstance(ctx.processes[account["id"]], _FakePopen)
    ctx.stop_worker(account["id"])


def test_start_worker_refuses_archived_busy_port_cap_and_low_ram(loaded, monkeypatch, tmp_path):
    client, ctx = loaded
    account = _account(ctx, client, _auth(client), "Guards 2")
    _prepared(ctx, monkeypatch, tmp_path)

    archived = dict(account)
    archived["archived_at"] = "2026-01-01"
    monkeypatch.setattr(ctx, "query_account", lambda aid: archived)
    with pytest.raises(RuntimeError, match="archived"):
        worker_process.start_worker(ctx, account, manual=True)
    monkeypatch.undo()
    # Rebuild the account after undo.
    account = _account(ctx, client, _auth(client), "Guards 3")
    _prepared(ctx, monkeypatch, tmp_path)

    monkeypatch.setattr(ctx, "port_available", lambda port: False)
    monkeypatch.setattr(ctx, "reclaim_orphan_worker", lambda acc: False)
    with pytest.raises(RuntimeError, match="does not belong"):
        worker_process.start_worker(ctx, account, manual=True)
    monkeypatch.undo()
    account = _account(ctx, client, _auth(client), "Guards 4")
    _prepared(ctx, monkeypatch, tmp_path, cap=1)
    ctx.processes["other"] = _FakePopen()
    with pytest.raises(RuntimeError, match="Safety limit"):
        worker_process.start_worker(ctx, account, manual=True)
    ctx.processes.pop("other")
    monkeypatch.undo()
    account = _account(ctx, client, _auth(client), "Guards 5")
    _prepared(ctx, monkeypatch, tmp_path, memory=10**9)
    monkeypatch.setattr(
        worker_process.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=1),
    )
    with pytest.raises(RuntimeError, match="free RAM"):
        worker_process.start_worker(ctx, account, manual=True)


def test_stop_worker_terminates_fake_process_and_clears_state(loaded):
    _, ctx = loaded
    proc = _FakePopen()
    ctx.processes["acct"] = proc
    ctx.log_handles["acct"] = None
    worker_process.stop_worker(ctx, "acct")
    assert "acct" not in ctx.processes
    assert "acct" in ctx.suppressed_workers
    assert proc.terminated or sys.platform != "win32"


def test_reclaim_orphan_worker_is_a_noop_when_the_port_is_free(loaded, monkeypatch):
    _, ctx = loaded
    account = {"id": "a", "port": 45001}
    monkeypatch.setattr(ctx, "port_available", lambda port: True)
    # Nothing to reclaim: the port is already free, so the caller starts normally.
    assert worker_process.reclaim_orphan_worker(ctx, account) is False
    monkeypatch.setattr(ctx, "port_available", lambda port: False)
    monkeypatch.setattr(worker_process.psutil, "process_iter", lambda attrs: [])
    assert worker_process.reclaim_orphan_worker(ctx, account) is False


def test_focus_worker_window_noop_without_process(loaded):
    _, ctx = loaded
    assert worker_process.focus_worker_window(ctx, {"id": "missing"}) is False
