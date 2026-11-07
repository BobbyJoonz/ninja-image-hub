"""Reference-image uploads: limits, storage, job wiring, gateway multipart, cleanup."""
import base64
import importlib
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hub import uploads  # noqa: E402


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


def png_bytes(size=(64, 48), color="green"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(size=(40, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def webp_bytes(size=(40, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="WEBP")
    return buffer.getvalue()


def upload(client, headers, files, path="/api/uploads", auth_header=None):
    prepared = []
    for name, data in files:
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(Path(name).suffix.lower(), "application/octet-stream")
        prepared.append(("files", (name, data, mime)))
    return client.post(path, files=prepared, headers=auth_header or headers)


# ------------------------------------------------------------------ validation
def test_probe_image_accepts_provider_formats_and_rejects_the_rest(monkeypatch):
    meta = uploads.probe_image(png_bytes(), "cat.png")
    assert meta["mime"] == "image/png" and meta["width"] == 64
    assert uploads.probe_image(jpeg_bytes(), "cat.jpg")["mime"] == "image/jpeg"
    assert uploads.probe_image(webp_bytes(), "cat.webp")["mime"] == "image/webp"

    with pytest.raises(uploads.UploadError) as exc:
        uploads.probe_image(b"%PDF-1.7\n...", "notes.pdf")
    assert exc.value.status == 415
    with pytest.raises(uploads.UploadError):
        uploads.probe_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "x.svg")
    with pytest.raises(uploads.UploadError):
        uploads.probe_image(png_bytes()[:8] + b"corrupt", "broken.png")

    # An image with a lying extension is judged by content, not by name.
    assert uploads.probe_image(png_bytes(), "actually-jpeg.jpg")["mime"] == "image/png"
    # Path traversal in the display name is neutralised.
    assert "/" not in uploads.probe_image(png_bytes(), "../../etc/passwd.png")["name"]

    monkeypatch.setattr(uploads, "MAX_PIXELS", 100)
    with pytest.raises(uploads.UploadError) as exc:
        uploads.probe_image(png_bytes(), "huge.png")
    assert "pixel" in exc.value.message

    animated = io.BytesIO()
    frames = [Image.new("RGB", (32, 32), color) for color in ("red", "blue")]
    frames[0].save(animated, format="WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(uploads.UploadError):
        uploads.probe_image(animated.getvalue(), "anim.webp")


# ------------------------------------------------------------------ upload API
def test_upload_api_stores_reference_bytes_and_reports_metadata(loaded):
    client, ctx = loaded
    h = auth(client)
    response = upload(client, h, [("a.png", png_bytes()), ("b.webp", webp_bytes())])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["count"] == 2
    assert body["upload_id"]
    assert body["limits"]["max_files"] == 10
    assert {item["mime"] for item in body["files"]} == {"image/png", "image/webp"}
    assert all(len(item["sha256"]) == 64 for item in body["files"])

    directory = ctx.DATA / "uploads" / body["upload_id"]
    assert directory.is_dir()
    stored = list(directory.glob("*"))
    assert len(stored) == 3  # two images + meta.json
    assert (directory / "meta.json").is_file()
    assert client.get("/api/settings").json()["upload_usage"]["count"] == 1

    # Unauthenticated / CSRF-less uploads are refused.
    assert client.post("/api/uploads", files=[("files", ("x.png", png_bytes(), "image/png"))]).status_code in (401, 403)

    # Agent endpoint accepts the Bearer key.
    agent = upload(
        client,
        {},
        [("agent.png", png_bytes())],
        path="/v1/uploads",
        auth_header={"Authorization": "Bearer test-agent-key-98765"},
    )
    assert agent.status_code == 201, agent.text


def test_upload_limits_are_enforced_server_side(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    calls = {
        "upload_max_files": 2,
        "upload_max_file_mb": 1,
        "upload_max_total_mb": 2,
    }
    monkeypatch.setattr(ctx, "setting", lambda key: calls.get(key, 0))

    response = upload(client, h, [("a.png", png_bytes())] * 3)
    assert response.status_code == 422
    assert "Too many images" in response.text

    big = png_bytes() + b"\x00" * (1024 * 1024 + 10)
    response = upload(client, h, [("big.png", big)])
    assert response.status_code == 413
    assert "per-file" in response.text

    response = upload(client, h, [("a.png", png_bytes()), ("b.png", png_bytes())])
    # Tiny images are within every cap; this is the guard that the OTHER checks are not
    # simply rejecting everything.
    assert response.status_code == 201, response.text

    response = upload(client, h, [("notes.pdf", b"%PDF-1.4")])
    assert response.status_code == 415

    response = upload(client, h, [("bad.png", b"\x89PNG\r\n\x1a\nbroken")])
    assert response.status_code == 422


def test_upload_request_body_limit(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    # Header gate: the middleware caps at (upload_max_total_mb + 2) MiB, so a 1 MiB
    # configured total rejects a 4 MiB request before it is parsed.
    monkeypatch.setattr(ctx, "setting", lambda key: 1 if key == "upload_max_total_mb" else 10)
    huge = b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024)
    response = upload(client, h, [("huge.png", huge)])
    assert response.status_code == 413


# ------------------------------------------------------------------ job wiring
def test_generate_with_upload_reference_and_idempotency(loaded):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Refs", "autostart": False}, headers=h).json()
    first = upload(client, h, [("a.png", png_bytes())]).json()["upload_id"]
    second = upload(client, h, [("b.png", png_bytes())]).json()["upload_id"]

    payload = {
        "account_id": acc["id"],
        "prompt": "use these references",
        "aspect_ratio": "1:1",
        "upload_id": first,
        "idempotency_key": "refs-key-0001",
    }
    queued = client.post("/api/generate", json=payload, headers=h)
    assert queued.status_code == 202, queued.text
    job = client.get(f"/api/jobs/{queued.json()['id']}").json()
    assert job["reference_count"] == 1
    assert job["reference_upload_id"] == first

    # Same key, same upload -> deduplicated; same key, different upload -> 409.
    again = client.post("/api/generate", json=payload, headers=h)
    assert again.json()["deduplicated"] is True
    conflict = client.post(
        "/api/generate", json={**payload, "upload_id": second}, headers=h
    )
    assert conflict.status_code == 409

    # Missing/expired upload id is refused before anything is queued.
    missing = client.post(
        "/api/generate",
        json={**payload, "upload_id": "00000000-0000-0000-0000-000000000000",
              "idempotency_key": "refs-key-0002"},
        headers=h,
    )
    assert missing.status_code == 422

    # Delete is refused while a queued job still references the upload.
    assert client.request("DELETE", f"/api/uploads/{first}", headers=h).status_code == 409


def test_delete_unused_upload(loaded):
    client, _ = loaded
    h = auth(client)
    upload_id = upload(client, h, [("a.png", png_bytes())]).json()["upload_id"]
    assert client.request("DELETE", f"/api/uploads/{upload_id}", headers=h).status_code == 200
    assert client.request("DELETE", f"/api/uploads/{upload_id}", headers=h).status_code == 404
    assert client.request("DELETE", "/api/uploads/not-a-uuid", headers=h).status_code == 404


# ------------------------------------------------------------------ end to end gateway multipart
def _mock_gateway(tmp_path, fail=False):
    vendor = tmp_path / "fake-gateway"
    (vendor / "src").mkdir(parents=True)
    (vendor / "node_modules").mkdir()
    (vendor / "public").mkdir()
    png = io.BytesIO()
    Image.new("RGB", (9, 7), "green").save(png, format="PNG")
    encoded = base64.b64encode(png.getvalue()).decode()
    capture = vendor / "requests.jsonl"
    node_js = r"""const http=require('http'), fs=require('fs');
const PORT=process.env.PORT;
const CAPTURE=__CAPTURE__;
const FAIL=__FAIL__;
const app=http.createServer((req,res)=>{
  if(req.url.startsWith('/health')) { res.setHeader('Content-Type','application/json');res.end('{"status":"ok"}');return; }
  if(req.url.startsWith('/metrics')) { res.setHeader('Content-Type','text/plain');res.end('gpt_web_gateway_session_valid 1\n');return; }
  if(req.url.startsWith('/login/screenshot')) {res.setHeader('Content-Type','image/jpeg');res.end(Buffer.from('ffd8ffd9','hex'));return;}
  if(req.url==='/login' && req.method==='GET') {res.setHeader('Content-Type','text/html');res.end('<html>fake login</html>');return;}
  if(req.url.startsWith('/login/')) {res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
  if(req.url.startsWith('/v1/images/generations')) {
    if(req.headers.authorization !== 'Bearer '+process.env.API_KEY){res.writeHead(401);res.end();return;}
    let body=[]; req.on('data',(c)=>body.push(c)); req.on('end',()=>{
      fs.appendFileSync(CAPTURE, JSON.stringify({ content_type: req.headers['content-type']||'', body_b64: Buffer.concat(body).toString('base64') })+'\n');
      if(FAIL){res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'boom'}}));return;}
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({data:[{b64_json:__ENCODED__}], applied:{thinking_mode:'extended',thinking_verified:true,requested_verified:true,thinking_fallback:null,thinking_mode_mixed:false}, requested:{thinking_mode:'extended'}}));
    });
    return;
  }
  res.writeHead(404);res.end('not found');
});
app.listen(PORT, (err) => { if(err) throw err; });
""".replace("__ENCODED__", json.dumps(encoded)).replace("__CAPTURE__", json.dumps(str(capture))).replace("__FAIL__", "true" if fail else "false")
    (vendor / "src" / "server.js").write_text(node_js)
    return vendor, capture


def _start_mock_account(client, h, ctx, monkeypatch, tmp_path, fail=False):
    vendor, capture = _mock_gateway(tmp_path, fail=fail)
    monkeypatch.setattr(ctx, "VENDOR", vendor)
    monkeypatch.setenv("DISPLAY", ":test-mock-only")
    acc = client.post("/api/accounts", json={"name": "Ref mock", "autostart": False}, headers=h).json()
    assert client.post(f"/api/accounts/{acc['id']}/start", headers=h).status_code == 200
    state = None
    for _ in range(120):
        state = client.get("/api/accounts").json()[0]
        if state["runtime"] == "online":
            break
        time.sleep(0.08)
    assert state["runtime"] == "online", state
    return acc, capture


def _wait_job(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "partial", "failed", "unknown", "rate_limited"):
            return job
        time.sleep(0.04)
    raise AssertionError("job did not settle")


def test_reference_images_reach_the_gateway_as_multipart(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc, capture = _start_mock_account(client, h, ctx, monkeypatch, tmp_path)
    upload_id = upload(
        client, h, [("ref-one.png", png_bytes()), ("ref-two.jpg", jpeg_bytes())]
    ).json()["upload_id"]

    response = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "blend the two references",
            "aspect_ratio": "1:1",
            "upload_id": upload_id,
            "idempotency_key": "refs-e2e-0001",
        },
        headers=h,
    )
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["id"])
    assert job["status"] == "completed", job
    assert job["reference_count"] == 2
    assert job["thinking_verified"] == 1

    requests = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines() if line]
    assert len(requests) == 1
    multipart = requests[0]
    assert multipart["content_type"].startswith("multipart/form-data")
    body = base64.b64decode(multipart["body_b64"]).decode("latin1")
    assert body.count('name="reference_images"') == 2
    assert "ref-one.png" in body and "ref-two.jpg" in body
    assert 'name="thinking_mode"' in body and "extended" in body
    assert 'name="prompt"' in body

    # The upload workspace is removed once the job is terminal.
    assert not (ctx.DATA / "uploads" / upload_id).exists()


def test_unknown_outcome_keeps_references_for_reconciliation(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc, _capture = _start_mock_account(client, h, ctx, monkeypatch, tmp_path, fail=True)
    upload_id = upload(client, h, [("ref.png", png_bytes())]).json()["upload_id"]
    response = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "this will lose its response",
            "aspect_ratio": "1:1",
            "upload_id": upload_id,
            "idempotency_key": "refs-e2e-0002",
        },
        headers=h,
    )
    job = _wait_job(client, response.json()["id"])
    assert job["status"] == "unknown", job
    assert (ctx.DATA / "uploads" / upload_id).exists()


def test_expired_upload_fails_before_any_upstream_request(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    client.patch("/api/control", json={"dispatch_paused": True}, headers=h)
    acc = client.post("/api/accounts", json={"name": "Expired", "autostart": False}, headers=h).json()
    upload_id = upload(client, h, [("ref.png", png_bytes())]).json()["upload_id"]
    response = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "reference will vanish",
            "aspect_ratio": "1:1",
            "upload_id": upload_id,
            "idempotency_key": "refs-e2e-0003",
        },
        headers=h,
    )
    assert response.status_code == 202
    # Simulate retention/cleanup winning the race before dispatch.
    import shutil

    shutil.rmtree(ctx.DATA / "uploads" / upload_id)
    client.patch("/api/control", json={"dispatch_paused": False}, headers=h)
    job = _wait_job(client, response.json()["id"])
    assert job["status"] == "failed"
    assert "expired before dispatch" in job["error"]


# ------------------------------------------------------------------ maintenance
def test_upload_sweep_is_age_and_usage_aware(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    old_upload = upload(client, h, [("old.png", png_bytes())]).json()["upload_id"]
    fresh_upload = upload(client, h, [("new.png", png_bytes())]).json()["upload_id"]
    import os
    import time as _time

    old_dir = ctx.DATA / "uploads" / old_upload
    stamp = _time.time() - 3 * 86400
    for path in old_dir.rglob("*"):
        os.utime(path, (stamp, stamp))
    os.utime(old_dir, (stamp, stamp))

    assert ctx.sweep_uploads(24) == 1
    assert not old_dir.exists()
    assert (ctx.DATA / "uploads" / fresh_upload).exists()

    # A live job pins its upload even when it looks old.
    client.patch("/api/control", json={"dispatch_paused": True}, headers=h)
    acc = client.post("/api/accounts", json={"name": "Pinned", "autostart": False}, headers=h).json()
    pinned = upload(client, h, [("pin.png", png_bytes())]).json()["upload_id"]
    client.post(
        "/api/generate",
        json={"account_id": acc["id"], "prompt": "pinned refs", "aspect_ratio": "1:1",
              "upload_id": pinned, "idempotency_key": "refs-pin-0001"},
        headers=h,
    )
    pin_dir = ctx.DATA / "uploads" / pinned
    for path in pin_dir.rglob("*"):
        os.utime(path, (stamp, stamp))
    os.utime(pin_dir, (stamp, stamp))
    assert ctx.sweep_uploads(24) == 0
    assert pin_dir.exists()


# ------------------------------------------------------------------ UI contract
def test_upload_ui_contract():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "modules" / "api.js").read_text(encoding="utf-8")
    views = (ROOT / "frontend" / "modules" / "views.js").read_text(encoding="utf-8")
    assert 'id="genFiles"' in html and 'multiple' in html
    assert 'accept="image/png,image/jpeg,image/webp"' in html
    assert "fileChips" in html and "fileLimits" in html
    assert "apiUpload" in api and "apiUpload" in app
    assert "reference_count" in views
    css = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
    assert ".file-drop" in css and ".file-chip" in css

# ------------------------------------------------------------------ deep-review additions
def _noise_png(size=(700, 700)):
    import os as _os

    buffer = io.BytesIO()
    raw = _os.urandom(size[0] * size[1] * 3)
    Image.frombytes("RGB", size, raw).save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_meta_and_safe_names(loaded):
    client, ctx = loaded
    h = auth(client)
    response = upload(client, h, [("my cat.png", _noise_png((64, 64)))])
    assert response.status_code == 201, response.text
    meta = response.json()
    item = meta["files"][0]
    assert item["name"] == "my cat.png"
    assert item["safe_name"] == "1-my cat.png"
    record = ctx.load_upload(meta["upload_id"])
    assert record is not None
    assert record["files"][0]["file"].startswith("1-")
    if os.name != "nt":
        directory = ctx.DATA / "uploads" / meta["upload_id"]
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / record["files"][0]["file"]).stat().st_mode & 0o777 == 0o600


def test_total_upload_cap_is_enforced_by_route(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    monkeypatch.setattr(
        ctx,
        "setting",
        lambda key: {
            "upload_max_files": 10,
            "upload_max_file_mb": 20,
            "upload_max_total_mb": 1,
        }.get(key, 0),
    )
    first, second = _noise_png((700, 700)), _noise_png((700, 700))
    assert len(first) + len(second) > 1024 * 1024
    response = upload(client, h, [("a.png", first), ("b.png", second)])
    assert response.status_code == 413, response.text
    assert "total" in response.text.lower()


def test_chunked_upload_without_content_length_is_rejected(loaded):
    client, _ = loaded
    h = auth(client)

    def body():
        yield b"--x\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.png\"\r\n"
        yield b"Content-Type: image/png\r\n\r\n"
        yield b"\x89PNG\r\n\x1a\n"
        yield b"\r\n--x--\r\n"

    response = client.post(
        "/api/uploads",
        content=body(),
        headers={**h, "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 411, response.text


def test_canceling_queued_job_releases_its_upload(loaded):
    client, ctx = loaded
    h = auth(client)
    client.patch("/api/control", json={"dispatch_paused": True}, headers=h)
    acc = client.post("/api/accounts", json={"name": "Cancel refs", "autostart": False}, headers=h).json()
    upload_id = upload(client, h, [("ref.png", _noise_png((64, 64)))]).json()["upload_id"]
    job = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "cancel me",
            "upload_id": upload_id,
            "idempotency_key": "cancel-refs-0001",
        },
        headers=h,
    ).json()
    assert (ctx.DATA / "uploads" / upload_id).exists()
    assert client.delete(f"/api/jobs/{job['id']}", headers=h).status_code == 200
    assert not (ctx.DATA / "uploads" / upload_id).exists()


def test_deleting_unknown_job_releases_its_upload(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc, _capture = _start_mock_account(client, h, ctx, monkeypatch, tmp_path, fail=True)
    upload_id = upload(client, h, [("ref.png", _noise_png((64, 64)))]).json()["upload_id"]
    job = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "unknown then delete",
            "upload_id": upload_id,
            "idempotency_key": "unknown-refs-0001",
        },
        headers=h,
    ).json()
    settled = _wait_job(client, job["id"])
    assert settled["status"] == "unknown"
    assert (ctx.DATA / "uploads" / upload_id).exists()
    assert client.delete(f"/api/jobs/{job['id']}", headers=h).status_code == 200
    assert not (ctx.DATA / "uploads" / upload_id).exists()


def test_purge_account_removes_referenced_uploads(loaded):
    client, ctx = loaded
    h = auth(client)
    acc = client.post("/api/accounts", json={"name": "Purge refs", "autostart": False}, headers=h).json()
    upload_id = upload(client, h, [("ref.png", _noise_png((64, 64)))]).json()["upload_id"]
    assert client.post(f"/api/accounts/{acc['id']}/archive", headers=h).status_code == 200
    import uuid
    from sqlalchemy import insert

    with ctx.engine.begin() as conn:
        conn.execute(
            insert(ctx.jobs).values(
                id=str(uuid.uuid4()),
                account_id=acc["id"],
                prompt="archived refs",
                aspect_ratio="1:1",
                quality_preference="best",
                status="canceled",
                n=1,
                reference_upload_id=upload_id,
                reference_count=1,
                created_at=ctx.now(),
                finished_at=ctx.now(),
            )
        )
    assert (ctx.DATA / "uploads" / upload_id).exists()
    purge = client.post(
        f"/api/accounts/{acc['id']}/purge",
        json={"confirmation": "DELETE Purge refs"},
        headers=h,
    )
    assert purge.status_code == 200, purge.text
    assert not (ctx.DATA / "uploads" / upload_id).exists()


def test_duplicate_names_get_distinct_safe_names(loaded):
    client, _ = loaded
    h = auth(client)
    response = upload(
        client,
        h,
        [("same.png", _noise_png((32, 32))), ("same.png", _noise_png((33, 33)))],
    )
    assert response.status_code == 201, response.text
    assert [item["safe_name"] for item in response.json()["files"]] == [
        "1-same.png",
        "2-same.png",
    ]


def test_agent_upload_then_generate(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    acc, capture = _start_mock_account(client, h, ctx, monkeypatch, tmp_path)
    bearer = {"Authorization": "Bearer test-agent-key-98765"}
    uploaded = upload(
        client,
        {},
        [("agent-ref.png", _noise_png((64, 64)))],
        path="/v1/uploads",
        auth_header=bearer,
    )
    assert uploaded.status_code == 201, uploaded.text
    upload_id = uploaded.json()["upload_id"]
    created = client.post(
        "/v1/images/generations",
        json={
            "account_id": acc["id"],
            "prompt": "agent with reference",
            "upload_id": upload_id,
            "idempotency_key": "agent-refs-0001",
        },
        headers=bearer,
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    job = None
    for _ in range(500):
        job = client.get(f"/v1/jobs/{job_id}", headers=bearer).json()
        if job["status"] in ("completed", "failed", "unknown", "partial", "rate_limited"):
            break
        time.sleep(0.04)
    assert job["status"] == "completed", job
    assert job["reference_count"] == 1
    requests = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines() if line]
    body = base64.b64decode(requests[-1]["body_b64"]).decode("latin1")
    assert 'name="reference_images"' in body
    assert "agent-ref.png" in body
    assert not (ctx.DATA / "uploads" / upload_id).exists()


def test_sweep_cleans_half_written_workspaces(loaded):
    client, ctx = loaded
    root = ctx.DATA / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    incoming = root / ".incoming-crashed"
    incoming.mkdir()
    (incoming / "raw-1.tmp").write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(incoming, (old, old))
    assert ctx.sweep_uploads(0) == 1
    assert not incoming.exists()

def test_reference_thumbnails_are_bounded_in_the_browser():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "createImageBitmap" in app
    assert "referencePreviewUrl" in app
    assert "bitmap.close" in app
    # The uploaded File object must stay untouched; only the chip preview is resized.
    assert "resizeWidth" not in app  # we scale by canvas, keeping aspect ratio
