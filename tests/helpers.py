"""Shared test scaffolding: app factory, auth helper and fake gateway.

Keeping these here removes the copies that used to live in every test module (and the
drift between them). Tests that need different behaviour monkeypatch the returned runtime.
"""

import base64
import importlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fake_gateway import png_bytes, write_fake_gateway  # noqa: E402
from fastapi.testclient import TestClient
from PIL import Image

ADMIN_PASSWORD = "example-passphrase-123456789"
AGENT_KEY = "test-agent-key-98765"


def build_hub(
    monkeypatch,
    tmp_path,
    *,
    runtime_installed=True,
    password=ADMIN_PASSWORD,
    db_url=None,
    vendor=None,
):
    """Create an isolated Hub app runtime for one test."""
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    monkeypatch.setenv("APP_SECRET", "a" * 64)
    monkeypatch.setenv("AGENT_API_KEY", AGENT_KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", db_url or "sqlite:///" + str(tmp_path / "hub.db"))
    monkeypatch.setenv("GATEWAY_PORT_BASE", "43000")
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    ctx = module.app.state.hub
    ctx.app = module.app
    if vendor is not None:
        monkeypatch.setattr(ctx, "VENDOR", vendor)
    if runtime_installed is not None:
        monkeypatch.setattr(ctx, "runtime_installed", lambda: runtime_installed)
    # pytest's tmp_path lives on the system drive and workers add up across the suite; a
    # busy CI/dev machine must not make upload/dispatch tests depend on host free space/RAM.
    # Both guards are tested explicitly with patched settings in test_phase4.
    from sqlalchemy import update

    for key in ("min_free_disk_mb", "min_free_memory_mb"):
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.control_settings)
                .where(ctx.control_settings.c.key == key)
                .values(value="0")
            )
        with ctx.config_guard:
            ctx.config_cache[key] = "0"
    ctx.disk_low = False
    return module, ctx


def client(module):
    return TestClient(module.app)


def auth(client, password=ADMIN_PASSWORD):
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    return {"x-csrf-token": response.json()["csrf"]}


def fake_gateway(tmp_path, **kwargs):
    """Backward-compatible wrapper around the one shared fake-gateway builder."""
    return write_fake_gateway(tmp_path, **kwargs)


def start_mock_account(client, headers, ctx, monkeypatch, tmp_path, *, fail=False, **kwargs):
    """Start an account whose worker points at a fake gateway; wait until online."""
    vendor, capture = fake_gateway(tmp_path, fail=fail, **kwargs)
    monkeypatch.setattr(ctx, "VENDOR", vendor)
    monkeypatch.setenv("DISPLAY", ":test-mock-only")
    account = client.post(
        "/api/accounts", json={"name": "Mock worker", "autostart": False}, headers=headers
    ).json()
    assert client.post(f"/api/accounts/{account['id']}/start", headers=headers).status_code == 200
    state = None
    for _ in range(150):
        state = [a for a in client.get("/api/accounts").json() if not a["archived_at"]][0]
        if state["runtime"] == "online":
            break
        import time

        time.sleep(0.08)
    assert state["runtime"] == "online", state
    return account, capture


def all_css():
    """Every shipped stylesheet, concatenated for contract tests."""
    root = Path(__file__).resolve().parents[1] / "frontend"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "styles").glob("*.css"))
    )
