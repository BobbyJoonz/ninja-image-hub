"""Worker process/setup units that do not launch a real browser."""
import socket
import sys
from types import SimpleNamespace

import pytest

from hub import worker_process, worker_setup


def test_display_mode_and_worker_command_for_every_platform():
    assert worker_setup.display_mode("win32", which=lambda _: None, env={}) == "desktop"
    assert (
        worker_setup.display_mode("linux", which=lambda _: None, env={"DISPLAY": ":0"})
        == "x11"
    )
    assert worker_setup.display_mode("linux", which=lambda _: "/usr/bin/xvfb-run", env={}) == "xvfb"
    assert worker_setup.display_mode("linux", which=lambda _: None, env={}) == "missing"
    assert worker_setup.worker_command("win32", which=lambda _: None, env={}) == [
        "node",
        "src/server.js",
    ]
    assert worker_setup.worker_command("linux", which=lambda _: "/usr/bin/xvfb-run", env={})[:2] == [
        "xvfb-run",
        "-a",
    ]


def test_worker_env_allows_no_parent_secrets_and_passes_the_wait_cap(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Env", "autostart": False}, headers=h).json()
    monkeypatch.setenv("APP_SECRET", "do-not-leak")
    monkeypatch.setenv("ADMIN_PASSWORD", "do-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    env = worker_setup.worker_env(ctx, ctx.query_account(acc["id"]))
    assert "APP_SECRET" not in env and "ADMIN_PASSWORD" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["PORT"] == str(ctx.query_account(acc["id"])["port"])
    assert env["RENDER_WAIT_SEC"] == str(ctx.setting("render_wait_sec"))


def test_read_auto_login_rejects_missing_and_malformed_files(loaded):
    _, ctx = loaded
    assert worker_setup._read_auto_login(ctx, "no-such-account") is None
    folder = ctx.DATA / "accounts" / "broken"
    folder.mkdir(parents=True)
    (folder / "auto-login.json").write_text("{not json", encoding="utf-8")
    assert worker_setup._read_auto_login(ctx, "broken") is None
    (folder / "auto-login.json").write_text('{"email": "a@b.c", "password": "x"}', encoding="utf-8")
    assert worker_setup._read_auto_login(ctx, "broken")["email"] == "a@b.c"


def test_listening_and_port_available(loaded):
    _, ctx = loaded
    assert worker_process.listening(ctx, 1) is False
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert worker_process.port_available(ctx, port) is True
    with socket.socket() as held:
        held.bind(("127.0.0.1", port))
        held.listen(1)
        assert worker_process.port_available(ctx, port) is False


def test_allocate_port_skips_used_and_busy_ports(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    client.post("/api/accounts", json={"name": "Port", "autostart": False}, headers=h)
    monkeypatch.setattr(ctx, "port_available", lambda port: port == 43005)
    assert worker_process.allocate_port(ctx) == 43005
    monkeypatch.setattr(ctx, "port_available", lambda port: False)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        worker_process.allocate_port(ctx)


def test_worker_rss_returns_none_for_dead_or_missing_workers(loaded):
    _, ctx = loaded
    assert worker_process.worker_rss_mb(ctx, "missing") is None

    class Dead:
        def poll(self):
            return 1

    ctx.processes["dead"] = Dead()
    assert worker_process.worker_rss_mb(ctx, "dead") is None


def test_wake_worker_async_is_rate_limited(loaded, monkeypatch):
    _, ctx = loaded
    calls = []
    monkeypatch.setattr(ctx, "start_worker_async", lambda acc, manual=False: calls.append(acc["id"]))
    account = {"id": "acct"}
    assert worker_process.wake_worker_async(ctx, account) is True
    assert worker_process.wake_worker_async(ctx, account) is False  # backoff window
    ctx.wake_state["acct"]["next"] = 0
    assert worker_process.wake_worker_async(ctx, account) is True
    assert calls == ["acct", "acct"]
