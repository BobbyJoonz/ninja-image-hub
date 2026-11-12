"""Login/live routes: diagnostics mapping, save verdicts, live input/cursor/focus/page."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from hub import login


class _Resp:
    def __init__(self, payload=None, content=b"jpeg", status=200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status

    def json(self):
        return self._payload


def test_remote_diagnostics_routes_map_payloads_and_invalid_json(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Diag", "autostart": False}, headers=h).json()

    async def fake(account_id, action, data=None):
        return _Resp({"url": "https://chatgpt.com/", "pages": [], "plan": "plus", "settings": {}})

    monkeypatch.setattr(ctx, "upstream_login_call", fake)
    for path in ("remote/url", "remote/pages", "remote/plan", "remote/settings"):
        response = client.get(f"/api/accounts/{acc['id']}/{path}")
        assert response.status_code == 200, (path, response.text)

    class Invalid(_Resp):
        def json(self):
            raise ValueError("bad")

    async def invalid(account_id, action, data=None):
        return Invalid()

    monkeypatch.setattr(ctx, "upstream_login_call", invalid)
    for path in ("remote/url", "remote/pages", "remote/plan", "remote/settings"):
        response = client.get(f"/api/accounts/{acc['id']}/{path}")
        assert response.status_code == 502, (path, response.text)


def test_remote_actions_and_save_verdict(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Actions", "autostart": False}, headers=h).json()
    calls = []

    async def fake(account_id, action, data=None):
        calls.append(action)
        return _Resp(content=b"jpeg")

    monkeypatch.setattr(ctx, "upstream_login_call", fake)
    assert client.get(f"/api/accounts/{acc['id']}/remote/screenshot").status_code == 200
    assert client.post(f"/api/accounts/{acc['id']}/remote/click", json={"x": 1, "y": 2}, headers=h).status_code == 200
    assert client.post(f"/api/accounts/{acc['id']}/remote/type", json={"text": "hi"}, headers=h).status_code == 200
    assert client.post(f"/api/accounts/{acc['id']}/remote/key", json={"key": "Enter"}, headers=h).status_code == 200
    assert client.post(f"/api/accounts/{acc['id']}/remote/key", json={"key": "F1"}, headers=h).status_code == 422
    assert client.post(f"/api/accounts/{acc['id']}/remote/navigate", json={}, headers=h).status_code == 200

    monkeypatch.setattr(ctx, "session_snapshot", lambda account: {"state": "connected", "blocker": None})
    monkeypatch.setattr(ctx, "plan_snapshot", lambda account: None)
    saved = client.post(f"/api/accounts/{acc['id']}/remote/save", headers=h)
    assert saved.status_code == 200
    assert saved.json()["session"] == "connected"
    assert "screenshot" in calls and "save" in calls


def test_live_page_headers_and_cursor_focus(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Live", "autostart": False}, headers=h).json()
    page = client.get(f"/accounts/{acc['id']}/live")
    assert page.status_code == 200 and page.headers["x-frame-options"] == "DENY"
    assert client.get("/accounts/missing/live").status_code == 404

    class CursorClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return _Resp({"cursor": "pointer"})

    monkeypatch.setattr(login.httpx, "AsyncClient", CursorClient)
    cursor = client.get(f"/api/accounts/{acc['id']}/remote/cursor?x=1&y=2")
    assert cursor.status_code == 200 and cursor.json()["cursor"] == "pointer"

    monkeypatch.setattr(ctx, "focus_worker_window", lambda account: True, raising=False)
    focus = client.post(f"/api/accounts/{acc['id']}/remote/focus", headers=h)
    assert focus.status_code == 200 and focus.json()["restored"] is True


def test_live_input_applies_only_valid_events(loaded, monkeypatch):
    client, ctx = loaded
    h = {"x-csrf-token": client.post("/api/auth/login", json={"password": "example-passphrase-123456789"}).json()["csrf"]}
    acc = client.post("/api/accounts", json={"name": "Input", "autostart": False}, headers=h).json()
    sent = []

    async def fake(account_id, action, data=None):
        sent.append((action, data))
        return _Resp()

    monkeypatch.setattr(ctx, "upstream_login_call", fake)
    empty = client.post(f"/api/accounts/{acc['id']}/remote/input", json={"events": []}, headers=h)
    assert empty.status_code == 200 and empty.json()["applied"] == 0
    events = [
        {"type": "mouse", "action": "move", "x": 3, "y": 4},
        {"type": "key", "key": "Enter", "vk": 13},
        {"type": "text", "text": "hello"},
        {"type": "unknown"},
    ]
    applied = client.post(f"/api/accounts/{acc['id']}/remote/input", json={"events": events}, headers=h)
    assert applied.status_code == 200 and applied.json()["applied"] == 3
    assert sent and sent[0][0] == "input" and len(sent[0][1]["events"]) == 3
