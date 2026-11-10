"""Thinking-effort tier: mapping/verification, API setting, payload and echo persistence."""
import base64
import importlib
import io
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hub import effort  # noqa: E402
from helpers import all_css, auth, start_mock_account

def test_effort_setting_validates_and_persists(loaded):
    client, ctx = loaded
    h = auth(client)
    settings = client.get("/api/settings").json()
    assert settings["thinking_effort"] == "high"
    assert settings["thinking_effort_label"] == "High"

    assert client.patch("/api/control", json={"thinking_effort": "ultra"}, headers=h).status_code == 422
    patched = client.patch("/api/control", json={"thinking_effort": "extra_high"}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["config"]["thinking_effort"] == "extra_high"
    assert client.get("/api/settings").json()["thinking_effort_label"] == "Extra High"
    # Integer validation must keep working for the numeric settings.
    assert client.patch("/api/control", json={"global_max_inflight": 6}, headers=h).status_code == 200
    assert ctx.setting("global_max_inflight") == 6
    assert ctx.setting("thinking_effort") == "extra_high"


# ------------------------------------------------------------------ end to end with a fake gateway
def test_generation_sends_high_and_records_what_the_ui_applied(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    verified = {
        "thinking_mode": "extended",
        "thinking_verified": True,
        "requested_verified": True,
        "thinking_fallback": None,
        "thinking_mode_mixed": False,
    }
    downgraded = {
        "thinking_mode": "standard",
        "thinking_verified": False,
        "requested_verified": False,
        "thinking_fallback": "standard",
        "thinking_mode_mixed": False,
    }
    acc, capture = start_mock_account(
        client,
        h,
        ctx,
        monkeypatch,
        tmp_path,
        responses=[
            {"status": 200, "applied": verified},
            {"status": 200, "applied": downgraded},
            {"status": 200, "applied": None},
            {"status": 200, "applied": verified},
        ],
    )

    def submit(prompt):
        response = client.post(
            "/api/generate",
            json={"account_id": acc["id"], "prompt": prompt, "aspect_ratio": "1:1"},
            headers=h,
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed", "partial", "unknown"):
                return job
            time.sleep(0.04)
        raise AssertionError("job did not settle")

    first = submit("effort probe one")
    assert first["status"] == "completed", first
    assert first["thinking_requested"] == "high"
    assert first["thinking_applied"] == "high"
    assert first["thinking_verified"] == 1
    assert not first["thinking_fallback"]

    downgraded = submit("effort probe two")
    assert downgraded["status"] == "completed"
    assert downgraded["thinking_requested"] == "high"
    assert downgraded["thinking_applied"] == "medium"
    assert downgraded["thinking_verified"] == 0
    assert downgraded["thinking_fallback"] == "standard"
    assert client.get("/api/health").json()["effort_below_requested_24h"] >= 1

    unknown = submit("effort probe three")
    assert unknown["status"] == "completed"
    assert unknown["thinking_verified"] is None
    assert unknown["thinking_applied"] is None

    # The operator can choose a different tier; the payload follows the setting.
    assert client.patch("/api/control", json={"thinking_effort": "extra_high"}, headers=h).status_code == 200
    fourth = submit("effort probe four")
    assert fourth["status"] == "completed"

    payloads = [json.loads(line)["json"] for line in capture.read_text(encoding="utf-8").splitlines() if line]
    assert [p.get("thinking_mode") for p in payloads] == [
        "extended",
        "extended",
        "extended",
        "extra_high",
    ]
    # A downgrade is recorded, never retried: exactly one upstream request per job.
    assert len(payloads) == 4
    assert client.post(f"/api/accounts/{acc['id']}/stop", headers=h).status_code == 200


def test_watch_alerts_on_effort_downgrade():
    spec = importlib.util.spec_from_file_location("watch_effort", ROOT / "scripts" / "watch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    healthy = {"accounts": 2, "queued": 0, "running_workers": 2, "effort_below_requested_24h": 0}
    assert watch.evaluate(healthy) == []
    downgraded = dict(healthy, effort_below_requested_24h=3)
    assert [a["key"] for a in watch.evaluate(downgraded)] == ["effort_downgrade"]


def test_effort_ui_contract():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    views = (ROOT / "frontend" / "modules" / "views.js").read_text(encoding="utf-8")
    assert 'name="thinking_effort"' in html
    assert 'value="high" selected' in html
    assert "effortBadge" in views and "thinking_verified" in views
    assert "effort-badge" in all_css()


def test_effort_mapping_and_verification():
    assert effort.gateway_mode("high") == "extended"
    assert effort.gateway_mode("extra_high") == "extra_high"
    assert effort.gateway_mode("medium") == "standard"
    assert effort.gateway_mode("instant") == "instant"
    assert effort.normalize_effort("High") == "high"
    assert effort.normalize_effort("extended") == "high"
    assert effort.normalize_effort("nonsense") == "high"  # safe default, never fails open lower
    assert effort.effort_label("high") == "High"

    verified = effort.summarize(
        {
            "thinking_mode": "extended",
            "thinking_verified": True,
            "thinking_fallback": None,
            "thinking_mode_mixed": False,
        },
        "high",
    )
    assert verified["applied"] == "high"
    assert verified["verified"] is True
    assert verified["degraded"] is False
    assert effort.badge(verified) == "High ✓"

    downgraded = effort.summarize(
        {
            "thinking_mode": "standard",
            "thinking_verified": False,
            "thinking_fallback": "standard",
        },
        "high",
    )
    assert downgraded["applied"] == "medium"
    assert downgraded["verified"] is False
    assert downgraded["degraded"] is True
    assert downgraded["fallback"] == "standard"
    assert "→" in effort.badge(downgraded) and "High" in effort.badge(downgraded)

    missing = effort.summarize(None, "high")
    assert missing["verified"] is None and missing["degraded"] is None
    assert effort.badge(missing) == "High ?"

    mixed = effort.summarize(
        {"thinking_mode": "extended", "thinking_verified": True, "thinking_mode_mixed": True},
        "high",
    )
    assert mixed["mixed"] is True and "mixed" in effort.badge(mixed)
