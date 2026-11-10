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


def png_bytes(size=(9, 7), color="green"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def fake_gateway(
    tmp_path,
    *,
    color="green",
    fail=False,
    responses=None,
    name="fake-gateway",
    image_bytes=None,
):
    """Write a minimal gateway that answers the endpoints the panel probes.

    Every generation request is captured to ``requests.jsonl`` as
    ``{content_type, body_b64, json}`` so tests can assert both JSON and multipart payloads.
    ``responses`` is a list of per-call outcomes:
      {"status": 200, "applied": {...} | None, "b64": true}
      {"status": 500, "body": {...}}
    When absent, one 200 response with a verified ``extended`` tier is used.
    """
    vendor = Path(tmp_path) / name
    (vendor / "src").mkdir(parents=True)
    (vendor / "node_modules").mkdir()
    (vendor / "public").mkdir()
    encoded = base64.b64encode(image_bytes or png_bytes(color=color)).decode()
    if responses is None:
        responses = [
            {
                "status": 200,
                "applied": {
                    "thinking_mode": "extended",
                    "thinking_verified": True,
                    "requested_verified": True,
                    "thinking_fallback": None,
                    "thinking_mode_mixed": False,
                },
            }
        ]
    if fail:
        responses = [{"status": 500, "body": {"error": {"message": "boom"}}}]
    capture = vendor / "requests.jsonl"
    node_js = r"""const http=require('http'), fs=require('fs');
const PORT=process.env.PORT;
const CAPTURE=__CAPTURE__;
const RESPONSES=__RESPONSES__;
let call=0;
const app=http.createServer((req,res)=>{
  if(req.url.startsWith('/health')) { res.setHeader('Content-Type','application/json');res.end('{"status":"ok"}');return; }
  if(req.url.startsWith('/metrics')) { res.setHeader('Content-Type','text/plain');res.end('gpt_web_gateway_session_valid 1\n');return; }
  if(req.url.startsWith('/login/screenshot')) {res.setHeader('Content-Type','image/jpeg');res.end(Buffer.from('ffd8ffd9','hex'));return;}
  if(req.url==='/login' && req.method==='GET') {res.setHeader('Content-Type','text/html');res.end('<html>fake login</html>');return;}
  if(req.url.startsWith('/login/')) {res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
  if(req.url.startsWith('/v1/images/generations')) {
    if(req.headers.authorization !== 'Bearer '+process.env.API_KEY){res.writeHead(401);res.end();return;}
    let chunks=[];
    req.on('data',(c)=>chunks.push(c));
    req.on('end',()=>{
      const raw=Buffer.concat(chunks);
      const contentType=req.headers['content-type']||'';
      let parsed=null;
      if(!contentType.toLowerCase().includes('multipart')) {
        try { parsed=JSON.parse(raw.toString('utf8')||'{}'); } catch(e) { parsed=null; }
      }
      fs.appendFileSync(CAPTURE, JSON.stringify({content_type:contentType, body_b64:raw.toString('base64'), json:parsed})+'\n');
      const spec=RESPONSES[Math.min(call, RESPONSES.length-1)]; call += 1;
      if(spec.status && spec.status !== 200) {
        res.writeHead(spec.status, {'Content-Type':'application/json'});
        res.end(JSON.stringify(spec.body || {error:{message:'gateway failed'}}));
        return;
      }
      const payload={data:[{b64_json:__ENCODED__}]};
      if(spec.applied) payload.applied=spec.applied;
      if(spec.requested !== false) payload.requested={thinking_mode:'extended'};
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify(payload));
    });
    return;
  }
  res.writeHead(404);res.end('not found');
});
app.listen(PORT, (err) => { if(err) throw err; });
""".replace("__ENCODED__", json.dumps(encoded)).replace(
        "__CAPTURE__", json.dumps(str(capture))
    ).replace("__RESPONSES__", json.dumps(responses))
    (vendor / "src" / "server.js").write_text(node_js, encoding="utf-8")
    return vendor, capture


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
