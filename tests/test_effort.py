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
    monkeypatch.setattr(ctx, "runtime_installed", lambda: True)
    with TestClient(module.app) as client:
        yield client, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)


def auth(client, password="example-passphrase-123456789"):
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    return {"x-csrf-token": response.json()["csrf"]}


# ------------------------------------------------------------------ pure mapping
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
def _mock_gateway(tmp_path):
    vendor = tmp_path / "fake-gateway"
    (vendor / "src").mkdir(parents=True)
    (vendor / "node_modules").mkdir()
    (vendor / "public").mkdir()
    png = io.BytesIO()
    Image.new("RGB", (9, 7), "blue").save(png, format="PNG")
    encoded = base64.b64encode(png.getvalue()).decode()
    capture = vendor / "payloads.jsonl"
    node_js = r"""const http=require('http'), fs=require('fs');
const PORT=process.env.PORT;
let calls=0;
const CAPTURE=__CAPTURE__;
const app=http.createServer((req,res)=>{
  let body='';
  req.on('data',(c)=>{body+=c});
  req.on('end',()=>{
    if(req.url.startsWith('/health')) { res.setHeader('Content-Type','application/json');res.end('{"status":"ok"}');return; }
    if(req.url.startsWith('/metrics')) { res.setHeader('Content-Type','text/plain');res.end('gpt_web_gateway_session_valid 1\n');return; }
    if(req.url.startsWith('/login/screenshot')) {res.setHeader('Content-Type','image/jpeg');res.end(Buffer.from('ffd8ffd9','hex'));return;}
    if(req.url==='/login' && req.method==='GET') {res.setHeader('Content-Type','text/html');res.end('<html>fake login</html>');return;}
    if(req.url.startsWith('/login/')) {res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
    if(req.url.startsWith('/v1/images/generations')) {
      if(req.headers.authorization !== 'Bearer '+process.env.API_KEY){res.writeHead(401);res.end();return;}
      let parsed={}; try { parsed=JSON.parse(body||'{}'); } catch(e) {}
      fs.appendFileSync(CAPTURE, JSON.stringify(parsed)+'\n');
      calls += 1;
      const mode = parsed.thinking_mode || 'extended';
      let applied;
      if (calls % 4 === 2) {
        applied = { thinking_mode:'standard', thinking_verified:false, requested_verified:false, thinking_fallback:'standard', thinking_mode_mixed:false };
      } else if (calls % 4 === 3) {
        applied = undefined;
      } else {
        applied = { thinking_mode:mode, thinking_verified:true, requested_verified:true, thinking_fallback:null, thinking_mode_mixed:false };
      }
      const payload = { data:[{b64_json:__ENCODED__}] };
      if (applied) payload.applied = applied;
      payload.requested = { thinking_mode: mode };
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify(payload));
      return;
    }
    res.writeHead(404);res.end('not found');
  });
});
app.listen(PORT, (err) => { if(err) throw err; });
""".replace("__ENCODED__", json.dumps(encoded)).replace("__CAPTURE__", json.dumps(str(capture)))
    (vendor / "src" / "server.js").write_text(node_js)
    return vendor, capture


def test_generation_sends_high_and_records_what_the_ui_applied(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    vendor, capture = _mock_gateway(tmp_path)
    monkeypatch.setattr(ctx, "VENDOR", vendor)
    monkeypatch.setenv("DISPLAY", ":test-mock-only")
    acc = client.post("/api/accounts", json={"name": "Effort mock", "autostart": False}, headers=h).json()
    assert client.post(f"/api/accounts/{acc['id']}/start", headers=h).status_code == 200
    for _ in range(120):
        state = client.get("/api/accounts").json()[0]
        if state["runtime"] == "online":
            break
        time.sleep(0.08)
    assert state["runtime"] == "online", state

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

    payloads = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines() if line]
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
    assert "effort-badge" in (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
