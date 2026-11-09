"""Failure classification: pre-submit/known failures are failed, true ambiguity is unknown."""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    with TestClient(module.app) as client:
        yield client, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def auth(client):
    response = client.post("/api/auth/login", json={"password": "example-passphrase-123456789"})
    return {"x-csrf-token": response.json()["csrf"]}


def _failed_job(client, ctx, monkeypatch, response, account):
    from hub import scheduler

    h = auth(client)
    client.post(f"/api/accounts/{account['id']}/start", headers=h)
    ctx.stop_scheduler()
    job = client.post(
        "/api/generate",
        json={"account_id": account["id"], "prompt": "failure classification probe"},
        headers=h,
    ).json()
    monkeypatch.setattr(ctx, "process_state", lambda a: "running")
    monkeypatch.setattr(ctx, "upstream_probe", lambda a: True)
    monkeypatch.setattr(scheduler, "request_generation", lambda *a, **k: response)
    ctx.execute_job(job["id"], ctx.query_account(account["id"]), "probe", "1:1", "draft", 1)
    return client.get(f"/api/jobs/{job['id']}").json()


@pytest.mark.parametrize(
    "kind",
    ["page_load_failed", "invalid_request", "upload_failed", "policy_violation", "image_generation_failed"],
)
def test_certain_gateway_failures_are_failed(loaded, monkeypatch, kind):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Fail", "autostart": False}, headers=h).json()
    response = SimpleNamespace(
        status_code=500,
        headers={},
        data={"ok": False, "error_kind": kind, "error": {"message": f"{kind} happened"}},
    )
    job = _failed_job(client, ctx, monkeypatch, response, acc)
    assert job["status"] == "failed", job
    assert kind in job["error"]
    assert "safe to re-submit" in job["error"]
    assert "Nothing was delivered" in job["error"]


@pytest.mark.parametrize(
    "kind",
    ["timeout", "server_error", ""],
)
def test_ambiguous_gateway_failures_stay_unknown(loaded, monkeypatch, kind):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Unknown", "autostart": False}, headers=h).json()
    data = {"ok": False, "error": {"message": "gateway broke"}}
    if kind:
        data["error_kind"] = kind
    response = SimpleNamespace(status_code=504, headers={}, data=data)
    job = _failed_job(client, ctx, monkeypatch, response, acc)
    assert job["status"] == "unknown", job
    assert "before retry" in job["error"]
    if kind:
        assert kind in job["error"]


def test_login_blocker_is_surfaced(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Blocker", "autostart": False}, headers=h).json()
    response = SimpleNamespace(
        status_code=500,
        headers={},
        data={
            "ok": False,
            "error_kind": "page_load_failed",
            "login_blocker": "captcha",
            "error": {"message": "ChatGPT page did not load"},
        },
    )
    job = _failed_job(client, ctx, monkeypatch, response, acc)
    assert job["status"] == "failed"
    assert "captcha" in job["error"]


def test_older_gateway_message_fallback():
    from hub import scheduler

    parsed = scheduler.parse_gateway_failure(
        500, {"error": {"message": "ChatGPT page did not load"}}
    )
    assert parsed["kind"] == "page_load_failed" and parsed["certain_failure"] is True
    parsed = scheduler.parse_gateway_failure(502, {"error": {"message": "boom"}})
    assert parsed["kind"] == "" and parsed["certain_failure"] is False


def test_rate_limit_uses_top_level_error_kind(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Limit", "autostart": False}, headers=h).json()
    response = SimpleNamespace(
        status_code=429,
        headers={"retry-after": "5"},
        data={"ok": False, "error_kind": "rate_limit", "error": {"message": "slow down"}},
    )
    job = _failed_job(client, ctx, monkeypatch, response, acc)
    assert job["status"] == "rate_limited", job
    account = [a for a in client.get("/api/accounts").json() if a["id"] == acc["id"]][0]
    assert account["cooldown_until"]
