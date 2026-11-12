"""Login routes and helpers: display notes, page headers, upstream error mapping, retries."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from hub import login


def test_display_note_covers_every_mode():
    for mode, needle in (
        ("xvfb", "virtual display"),
        ("missing", "Install xvfb"),
        ("x11", "X11 display"),
        ("desktop", "Chrome window"),
    ):
        note = login._display_note(SimpleNamespace(display_state=lambda m=mode: m))
        assert needle in note


def test_ensure_worker_awake_requires_runtime_and_reuses_healthy_workers():
    ctx = SimpleNamespace(
        runtime_installed=lambda: False,
        touch_account=lambda _id: None,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.ensure_worker_awake(ctx, {"id": "a"}))
    assert exc.value.status_code == 409

    ctx = SimpleNamespace(
        runtime_installed=lambda: True,
        touch_account=lambda _id: None,
        process_state=lambda acc: "running",
        upstream_probe=lambda acc: True,
    )
    asyncio.run(login.ensure_worker_awake(ctx, {"id": "a"}))  # no wake call needed


def test_ensure_worker_awake_times_out_with_a_clear_error():
    ctx = SimpleNamespace(
        runtime_installed=lambda: True,
        touch_account=lambda _id: None,
        process_state=lambda acc: "stopped",
        upstream_probe=lambda acc: False,
        wake_worker_async=lambda acc: True,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.ensure_worker_awake(ctx, {"id": "a"}, timeout=0.01))
    assert exc.value.status_code == 409
    assert "did not start" in exc.value.detail


def test_login_page_headers_and_missing_account(loaded):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Login", "autostart": False}, headers=h).json()
    page = client.get(f"/accounts/{acc['id']}/login")
    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in page.headers
    assert "Cache-Control" in page.headers
    assert client.get("/accounts/does-not-exist/login").status_code == 404


class _FakeResponse:
    def __init__(self, status=200, payload=None, content=b"jpeg"):
        self.status_code = status
        self._payload = payload or {}
        self.content = content

    def json(self):
        return self._payload


class _FakeClient:
    response = _FakeResponse()
    error = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


async def _awake(*args, **kwargs):
    return None


def _ctx_with_account(loaded):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Remote", "autostart": False}, headers=h).json()
    return ctx, acc


def test_upstream_login_call_maps_success_timeout_and_request_errors(loaded, monkeypatch):
    ctx, acc = _ctx_with_account(loaded)
    monkeypatch.setattr(login, "ensure_worker_awake", _awake)
    monkeypatch.setattr(login.httpx, "AsyncClient", _FakeClient)

    response = asyncio.run(login.upstream_login_call(ctx, acc["id"], "screenshot"))
    assert response.status_code == 200

    _FakeClient.error = httpx.TimeoutException("slow")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.upstream_login_call(ctx, acc["id"], "init"))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.upstream_login_call(ctx, acc["id"], "click"))
    assert exc.value.status_code == 504

    _FakeClient.error = httpx.ConnectError("down")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.upstream_login_call(ctx, acc["id"], "click"))
    assert exc.value.status_code == 503
    _FakeClient.error = None


def test_upstream_login_call_maps_upstream_409_for_screenshot(loaded, monkeypatch):
    ctx, acc = _ctx_with_account(loaded)
    monkeypatch.setattr(login, "ensure_worker_awake", _awake)
    monkeypatch.setattr(login.httpx, "AsyncClient", _FakeClient)
    _FakeClient.response = _FakeResponse(status=409, payload={"error": "no session"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login.upstream_login_call(ctx, acc["id"], "screenshot"))
    assert exc.value.status_code == 409
    _FakeClient.response = _FakeResponse()


def test_remote_init_retries_a_lease_race(loaded, monkeypatch):
    ctx, acc = _ctx_with_account(loaded)
    calls = []

    async def flaky(account_id, action):
        calls.append(action)
        if len(calls) == 1:
            raise HTTPException(409, "lease busy")

    monkeypatch.setattr(ctx, "upstream_login_call", flaky)
    assert asyncio.run(login.remote_init(ctx, acc["id"])) == {"ok": True}
    assert calls == ["init", "init"]
