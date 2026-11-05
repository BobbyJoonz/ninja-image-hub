import base64
import importlib
import io
import os
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "example-passphrase-123456789")
    monkeypatch.setenv("APP_SECRET", "a" * 64)
    monkeypatch.setenv("AGENT_API_KEY", "test-agent-key-98765")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "hub.db"))
    import sys

    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    mod = module.app.state.hub
    mod.app = module.app
    # Hermetic: never treat a real vendor/ checkout on the developer machine as installed.
    # A "runtime not installed" account must behave the same on every host. Tests that need
    # a gateway point VENDOR at their own fake vendor directory.
    monkeypatch.setattr(mod, "VENDOR", tmp_path / "vendor-not-installed")
    from sqlalchemy import select, update

    mod.select = select
    mod.update = update
    from hub.schemas import GenerateBody

    mod.GenerateBody = GenerateBody
    with TestClient(mod.app) as client:
        yield client, mod
    mod.engine.dispose()
    sys.modules.pop("app", None)


def auth(client):
    res = client.post(
        "/api/auth/login", json={"password": "example-passphrase-123456789"}
    )
    assert res.status_code == 200, res.text
    return {"x-csrf-token": res.json()["csrf"]}


def test_login_required_and_csrf(loaded):
    client, mod = loaded
    assert client.get("/api/accounts").status_code == 401
    assert (
        client.post("/api/auth/login", json={"password": "wrong-password"}).status_code
        == 401
    )
    h = auth(client)
    assert client.post("/api/accounts", json={"name": "Test A"}).status_code == 403
    assert (
        client.post("/api/accounts", json={"name": "Test A"}, headers=h).status_code
        == 201
    )
    assert client.get("/api/accounts").json()[0]["name"] == "Test A"
    assert "api_secret" not in client.get("/api/accounts").text


def test_create_multiple_isolated_accounts_and_jobs(loaded):
    client, mod = loaded
    h = auth(client)
    ids = []
    for name in ["Account A", "Account B", "Account C"]:
        r = client.post(
            "/api/accounts", json={"name": name, "autostart": False}, headers=h
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    assert len(set(ids)) == 3
    with mod.engine.connect() as c:
        accounts = [dict(r) for r in c.execute(mod.select(mod.accounts)).mappings()]
    assert len(set(a["port"] for a in accounts)) == 3
    assert len(set(a["api_secret"] for a in accounts)) == 3
    assert len(set(a["id"] for a in accounts)) == 3
    assert (
        client.post(
            "/api/generate",
            json={
                "account_id": ids[0],
                "prompt": "Generate this!",
                "aspect_ratio": "27:9",
            },
            headers=h,
        ).status_code
        == 422
    )
    response = client.post(
        "/api/generate",
        json={
            "account_id": ids[0],
            "prompt": "A purple panda in the snow",
            "aspect_ratio": "1:1",
        },
        headers=h,
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    import time

    for _ in range(30):
        job = client.get("/api/jobs/" + job_id).json()
        if job["status"] in ("failed", "completed"):
            break
        time.sleep(0.02)
    assert (
        job["status"] == "queued"
    )  # Offline jobs stay safely pending, without flooding upstream
    # Legacy DELETE cannot orphan untracked local session files. Use Archive instead.
    assert client.delete("/api/accounts/" + ids[0], headers=h).status_code == 410
    assert client.delete("/api/jobs/" + job_id, headers=h).status_code == 200
    assert (
        client.post("/api/accounts/" + ids[0] + "/archive", headers=h).status_code
        == 200
    )
    assert client.delete("/api/jobs/" + job_id, headers=h).status_code == 200
    assert client.get("/api/accounts").json()[0]["archived_at"] is not None
    assert client.get("/api/accounts").status_code == 200


def test_agent_endpoint_and_missing_session(loaded):
    client, mod = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "Agent worker", "autostart": False}, headers=h
    ).json()
    payload = {
        "account_id": acc["id"],
        "prompt": "A mountain scene",
        "aspect_ratio": "16:9",
    }
    assert client.post("/v1/images/generations", json=payload).status_code == 401
    r = client.post(
        "/v1/images/generations",
        json=payload,
        headers={"Authorization": "Bearer test-agent-key-98765"},
    )
    assert r.status_code == 202, r.text
    jobid = r.json()["id"]
    r = client.get(
        "/v1/jobs/" + jobid, headers={"Authorization": "Bearer test-agent-key-98765"}
    )
    assert r.status_code == 200
    assert r.json()["account_id"] == acc["id"]


def test_image_data_validation(loaded):
    _, m = loaded
    image = Image.new("RGB", (6, 6), "red")
    b = io.BytesIO()
    image.save(b, format="PNG")
    jobid = "00000000-0000-4000-8000-000000000001"
    filename, meta = m.write_image_payload(
        {"data": [{"b64_json": base64.b64encode(b.getvalue()).decode()}]}, jobid, 41000
    )
    assert (meta["image_width"], meta["image_height"], meta["image_format"]) == (
        6,
        6,
        "PNG",
    )
    assert filename == jobid + ".png"
    assert (m.DATA / "gallery" / filename).read_bytes() == b.getvalue()
    with pytest.raises(ValueError, match="unexpected image location"):
        m.write_image_payload(
            {"data": [{"url": "http://169.254.169.254/latest/meta-data"}]}, jobid, 41000
        )
    with pytest.raises(ValueError, match="non-image"):
        m.write_image_payload(
            {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]},
            jobid,
            41000,
        )


def test_login_worker_and_path_guard(loaded):
    client, mod = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", json={"name": "No worker", "autostart": False}, headers=h
    ).json()
    assert (
        client.get("/api/accounts/" + acc["id"] + "/remote/screenshot").status_code
        == 409
    )
    assert (
        client.post(
            "/api/accounts/" + acc["id"] + "/remote/click", json={"x": 100, "y": 200}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/accounts/" + acc["id"] + "/remote/click",
            json={"x": 100, "y": 200},
            headers=h,
        ).status_code
        == 409
    )
    assert (
        client.post("/api/accounts/" + acc["id"] + "/start", headers=h).status_code
        == 409
    )


def test_full_generation_path_with_isolated_mock_gateway(loaded, tmp_path, monkeypatch):
    """Full HTTP workflow using a fake Node upstream, NOT a real ChatGPT account."""
    client, m = loaded
    h = auth(client)
    vendor = tmp_path / "fake-gateway"
    (vendor / "src").mkdir(parents=True)
    (vendor / "node_modules").mkdir()
    (vendor / "public").mkdir()
    png = io.BytesIO()
    Image.new("RGB", (9, 7), "green").save(png, format="PNG")
    encoded = base64.b64encode(png.getvalue()).decode()
    node_js = r"""const http=require('http');
const PORT=process.env.PORT;
const app=http.createServer((req,res)=>{
  if(req.url.startsWith('/health')) { res.setHeader('Content-Type','application/json');res.end('{"status":"ok"}');return; }
  if(req.url.startsWith('/metrics')) { res.setHeader('Content-Type','text/plain');res.end('gpt_web_gateway_session_valid 1\n');return; }
  if(req.url.startsWith('/login/screenshot')) {res.setHeader('Content-Type','image/jpeg');res.end(Buffer.from('ffd8ffd9','hex'));return;}
  if(req.url==='/login' && req.method==='GET') {res.setHeader('Content-Type','text/html');res.end('<html>fake login</html>');return;}
  if(req.url.startsWith('/login/')) {res.setHeader('Content-Type','application/json');res.end('{"ok":true}');return;}
  if(req.url.startsWith('/v1/images/generations')) {
    if(req.headers.authorization !== 'Bearer '+process.env.API_KEY){res.writeHead(401);res.end();return;}
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({data:[{b64_json:'__ENCODED__'}]}));return;
  }
  res.writeHead(404);res.end('not found');
});
app.listen(PORT, (err) => { if(err) throw err; });
""".replace("__ENCODED__", encoded)
    (vendor / "src" / "server.js").write_text(node_js)
    monkeypatch.setattr(m, "VENDOR", vendor)
    monkeypatch.setenv("DISPLAY", ":test-mock-only")
    # xvfb-run works with the mock as well; this test intentionally uses real child process launch.
    acc = client.post(
        "/api/accounts", json={"name": "Mock worker", "autostart": False}, headers=h
    ).json()
    assert (
        client.post("/api/accounts/" + acc["id"] + "/start", headers=h).status_code
        == 200
    )
    import time

    for _ in range(100):
        st = client.get("/api/accounts").json()[0]
        if st["runtime"] == "online":
            break
        time.sleep(0.08)
    assert st["runtime"] == "online", st
    assert st["session"] == "connected"
    login = client.get("/accounts/" + acc["id"] + "/login")
    assert login.status_code == 200
    assert "Connect: Mock worker" in login.text
    assert client.get("/api/accounts/" + acc["id"] + "/remote/init").json() == {
        "ok": True
    }
    assert (
        client.get("/api/accounts/" + acc["id"] + "/remote/screenshot").status_code
        == 200
    )
    assert (
        client.post(
            "/api/accounts/" + acc["id"] + "/remote/save", headers=h
        ).status_code
        == 200
    )
    r = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "A mock mock green image",
            "aspect_ratio": "16:9",
        },
        headers=h,
    )
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    for _ in range(100):
        job = client.get("/api/jobs/" + jid).json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.04)
    assert job["status"] == "completed", job
    raw = client.get("/api/images/" + jid)
    assert raw.status_code == 200
    assert raw.content == png.getvalue()
    agent = client.get(
        "/v1/jobs/" + jid, headers={"Authorization": "Bearer test-agent-key-98765"}
    )
    assert agent.json()["image_url"] == "/v1/images/" + jid + "/0"
    assert client.get("/v1/images/" + jid).status_code == 401
    assert (
        client.get(
            "/v1/images/" + jid,
            headers={"Authorization": "Bearer test-agent-key-98765"},
        ).content
        == png.getvalue()
    )
    assert (
        client.post("/api/accounts/" + acc["id"] + "/stop", headers=h).status_code
        == 200
    )


def test_account_lifecycle_edit_archive_restore_and_purge(loaded):
    """Manage accounts and remove them without cross-account data loss."""
    client, mod = loaded
    headers = auth(client)
    a = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Studio One",
            "email_label": "one@example.test",
            "autostart": False,
        },
    ).json()
    b = client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Studio Two", "autostart": False},
    ).json()
    aid, bid = a["id"], b["id"]
    assert a["plan_label"] == "unknown"
    assert (
        client.patch(
            "/api/accounts/" + aid,
            headers=headers,
            json={
                "name": "Studio 01",
                "email_label": "edited@example.test",
                "autostart": False,
                "plan_label": "plus",
            },
        ).status_code
        == 200
    )
    updated = client.get("/api/accounts").json()[0]
    assert updated["plan_label"] == "plus" and updated["plan_checked_at"]
    assert updated["email_label"] == "edited@example.test"
    # Manual plan label is not a provider-verified subscription proof.
    assert (
        client.post("/api/accounts/" + aid + "/archive", headers=headers).status_code
        == 200
    )
    assert (
        client.post("/api/accounts/" + aid + "/start", headers=headers).status_code
        == 409
    )
    assert (
        client.post(
            "/api/generate",
            headers=headers,
            json={"account_id": aid, "prompt": "A poster", "aspect_ratio": "1:1"},
        ).status_code
        == 409
    )
    assert len(client.get("/api/accounts?include_archived=false").json()) == 1
    assert (
        client.post(
            "/api/accounts/" + aid + "/purge",
            headers=headers,
            json={"confirmation": "DELETE Studio One"},
        ).status_code
        == 422
    )
    assert (
        client.post("/api/accounts/" + aid + "/restore", headers=headers).status_code
        == 200
    )
    assert (
        client.get("/api/accounts?include_archived=false").json()[0]["archived_at"]
        is None
    )
    assert (
        client.post("/api/accounts/" + aid + "/archive", headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            "/api/accounts/" + aid + "/purge",
            headers=headers,
            json={"confirmation": "DELETE Studio 01"},
        ).status_code
        == 200
    )
    ids = [x["id"] for x in client.get("/api/accounts").json()]
    assert aid not in ids and bid in ids
    assert client.get("/api/accounts?include_archived=false").json()[0]["id"] == bid
    assert client.delete("/api/accounts/" + bid, headers=headers).status_code == 410


def test_purge_account_removes_only_own_gallery_and_session(loaded):
    client, mod = loaded
    headers = auth(client)
    a = client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Remove This", "autostart": False},
    ).json()
    b = client.post(
        "/api/accounts", headers=headers, json={"name": "Keep This", "autostart": False}
    ).json()
    keep = mod.DATA / "accounts" / b["id"]
    keep.mkdir(parents=True, exist_ok=True)
    (keep / "marker.txt").write_text("safe")
    remove = mod.DATA / "accounts" / a["id"]
    (remove / "gateway" / "auth").mkdir(parents=True)
    (remove / "gateway" / "auth" / "mock-cookie.txt").write_text("pretend secret")
    png = io.BytesIO()
    Image.new("RGB", (12, 10), "blue").save(png, format="PNG")
    raw = png.getvalue()
    filename = a["id"] + ".png"
    (mod.DATA / "gallery" / filename).write_bytes(raw)
    keep_file = b["id"] + ".png"
    (mod.DATA / "gallery" / keep_file).write_bytes(raw)
    with mod.engine.begin() as conn:
        for acc, file in [(a, filename), (b, keep_file)]:
            conn.execute(
                mod.jobs.insert().values(
                    id=str(__import__("uuid").uuid4()),
                    account_id=acc["id"],
                    prompt="A test image",
                    aspect_ratio="1:1",
                    quality_preference="best",
                    status="completed",
                    image_file=file,
                    created_at=mod.now(),
                )
            )
    assert (
        client.post(
            "/api/accounts/" + a["id"] + "/archive", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/accounts/" + a["id"] + "/purge",
            headers=headers,
            json={"confirmation": "DELETE Remove This"},
        ).status_code
        == 200
    )
    assert not (mod.DATA / "gallery" / filename).exists()
    assert not remove.exists()
    assert (mod.DATA / "gallery" / keep_file).read_bytes() == raw
    assert (keep / "marker.txt").read_text() == "safe"
    assert len(client.get("/api/jobs").json()) == 1


def test_quality_preference_and_original_image_metadata(loaded, tmp_path, monkeypatch):
    """Quality is a browser prompt preference; raw image bytes are preserved."""
    client, m = loaded
    h = auth(client)
    a = client.post(
        "/api/accounts", headers=h, json={"name": "Quality test", "autostart": False}
    ).json()
    assert (
        client.post(
            "/api/generate",
            headers=h,
            json={
                "account_id": a["id"],
                "prompt": "A nice landscape",
                "quality_preference": "ultra",
                "aspect_ratio": "1:1",
            },
        ).status_code
        == 422
    )
    raw = io.BytesIO()
    Image.new("RGB", (15, 17), "green").save(raw, format="PNG")
    raw = raw.getvalue()
    image_name, metadata = m.write_image_payload(
        {"data": [{"b64_json": base64.b64encode(raw).decode()}]},
        "00000000-0000-4000-8000-000000000099",
        41000,
    )
    assert (m.DATA / "gallery" / image_name).read_bytes() == raw
    assert metadata["image_width"] == 15 and metadata["image_height"] == 17
    import hashlib

    assert metadata["image_sha256"] == hashlib.sha256(raw).hexdigest()


def test_schema_migration_preserves_old_mvp_data(tmp_path):
    """Upgrading old SQLite database must not reset accounts or jobs."""
    import sqlite3, subprocess

    old_db = tmp_path / "old.db"
    c = sqlite3.connect(old_db)
    c.execute(
        "CREATE TABLE accounts (id VARCHAR(36) PRIMARY KEY, name VARCHAR(80) NOT NULL, email_label VARCHAR(160) NOT NULL, port INTEGER UNIQUE NOT NULL, api_secret VARCHAR(100) NOT NULL, autostart INTEGER NOT NULL, created_at VARCHAR(40) NOT NULL)"
    )
    c.execute(
        "CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY, account_id VARCHAR(36) NOT NULL, prompt TEXT NOT NULL, aspect_ratio VARCHAR(8) NOT NULL, status VARCHAR(20) NOT NULL, error TEXT, image_file VARCHAR(120), created_at VARCHAR(40) NOT NULL, started_at VARCHAR(40), finished_at VARCHAR(40))"
    )
    c.execute(
        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
        (
            "11111111-1111-4111-8111-111111111111",
            "Legacy",
            "",
            "41033",
            "secret",
            0,
            "2026-10-01T00:00:00+00:00",
        ),
    )
    c.commit()
    c.close()
    environ = dict(
        os.environ,
        DATA_DIR=str(tmp_path / "data"),
        DATABASE_URL="sqlite:///" + str(old_db),
        ADMIN_PASSWORD="strong-test-passphrase-567890",
        APP_SECRET="b" * 64,
        AGENT_API_KEY="private-test-agent-token",
    )
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            'import app; print(app.app.state.hub.query_account("11111111-1111-4111-8111-111111111111")["name"])',
        ],
        cwd=root,
        env=environ,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert run.returncode == 0, run.stderr
    assert "Legacy" in run.stdout
    c = sqlite3.connect(old_db)
    assert {"archived_at", "plan_label", "plan_checked_at"} <= set(
        x[1] for x in c.execute("PRAGMA table_info(accounts)")
    )
    assert {
        "quality_preference",
        "image_width",
        "image_height",
        "image_format",
        "image_bytes",
        "image_sha256",
    } <= set(x[1] for x in c.execute("PRAGMA table_info(jobs)"))
    assert c.execute("SELECT name FROM accounts").fetchall() == [("Legacy",)]
    c.close()


# v0.3 tests: these never access a real ChatGPT account.
def test_batch_images_are_all_preserved_byte_for_byte(loaded):
    client, m = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", headers=h, json={"name": "Batch owner", "autostart": False}
    ).json()
    jid = str(__import__("uuid").uuid4())
    with m.engine.begin() as conn:
        conn.execute(
            m.jobs.insert().values(
                id=jid,
                account_id=acc["id"],
                prompt="Two originals",
                aspect_ratio="1:1",
                status="running",
                quality_preference="best",
                n=2,
                created_at=m.now(),
            )
        )
    outputs = []
    for color in ["blue", "yellow"]:
        b = io.BytesIO()
        Image.new("RGB", (23, 19), color).save(b, format="PNG")
        outputs.append(b.getvalue())
    stored, err = m.record_image_batch(
        {"data": [{"b64_json": base64.b64encode(raw).decode()} for raw in outputs]},
        jid,
        41000,
        2,
    )
    assert err is None and len(stored) == 2
    result = client.get("/api/jobs/" + jid).json()
    assert len(result["images"]) == 2 and result["image_count"] == 2
    assert [client.get(f"/api/images/{jid}/{i}").content for i in range(2)] == outputs
    assert client.get(f"/api/images/{jid}/2").status_code == 404
    assert client.get(f"/v1/images/{jid}/1").status_code == 401
    assert (
        client.get(
            f"/v1/images/{jid}/1",
            headers={"Authorization": "Bearer test-agent-key-98765"},
        ).content
        == outputs[1]
    )
    with m.engine.begin() as conn:
        conn.execute(
            m.update(m.jobs).where(m.jobs.c.id == jid).values(status="completed")
        )
    assert client.delete("/api/jobs/" + jid, headers=h).status_code == 200
    assert all(not path.exists() for path in (m.DATA / "gallery").glob(jid + "*"))


def test_partial_batch_retains_existing_images(loaded):
    client, m = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", headers=h, json={"name": "Partial batch", "autostart": False}
    ).json()
    jid = str(__import__("uuid").uuid4())
    with m.engine.begin() as conn:
        conn.execute(
            m.jobs.insert().values(
                id=jid,
                account_id=acc["id"],
                prompt="Three variations",
                aspect_ratio="1:1",
                status="running",
                quality_preference="best",
                n=3,
                created_at=m.now(),
            )
        )
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(output, format="PNG")
    stored, err = m.record_image_batch(
        {
            "data": [
                {"b64_json": base64.b64encode(output.getvalue()).decode()},
                {"b64_json": base64.b64encode(b"invalid-image").decode()},
            ]
        },
        jid,
        41000,
        3,
    )
    assert len(stored) == 1 and "Image #2" in err
    assert client.get(f"/api/images/{jid}/0").content == output.getvalue()
    assert client.get(f"/api/jobs/{jid}").json()["image_count"] == 1


def test_idempotency_and_n_validation(loaded):
    client, m = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", headers=h, json={"name": "Idempotency", "autostart": False}
    ).json()
    payload = {
        "account_id": acc["id"],
        "prompt": "A poster with two variants",
        "n": 2,
        "idempotency_key": "request-2026-A001",
    }
    a = client.post("/api/generate", headers=h, json=payload)
    b = client.post("/api/generate", headers=h, json=payload)
    assert a.status_code == 202 and b.status_code == 202
    assert a.json()["id"] == b.json()["id"] and b.json()["deduplicated"] is True
    assert (
        client.post("/api/generate", headers=h, json=payload | {"n": 3}).status_code
        == 409
    )
    assert (
        client.post("/api/generate", headers=h, json=payload | {"n": 11}).status_code
        == 422
    )
    assert (
        client.post("/api/generate", headers=h, json=payload | {"n": 10}).status_code
        == 422
    )  # default cap 4
    assert client.get("/api/queue").json()["queued"] == 1
    # Queued jobs can be canceled, and afterward the account can safely be archived.
    assert client.delete("/api/jobs/" + a.json()["id"], headers=h).status_code == 200
    assert client.get("/api/jobs/" + a.json()["id"]).json()["status"] == "canceled"
    assert (
        client.post("/api/accounts/" + acc["id"] + "/archive", headers=h).status_code
        == 200
    )


def test_round_robin_dispatch_and_account_caps(loaded, monkeypatch):
    """Real scheduling threads, mocked provider only. Never treats browser tabs as unlimited."""
    import time, threading

    client, m = loaded
    h = auth(client)
    a = client.post(
        "/api/accounts",
        headers=h,
        json={"name": "Heavy", "parallel_limit": 2, "autostart": False},
    ).json()
    b = client.post(
        "/api/accounts",
        headers=h,
        json={"name": "Light", "parallel_limit": 2, "autostart": False},
    ).json()
    # Stage jobs while all accounts are offline: dispatcher must not consume any.
    a_ids = []
    for i in range(6):
        a_ids.append(
            client.post(
                "/api/generate",
                headers=h,
                json={"account_id": a["id"], "prompt": f"Art direction {i}"},
            ).json()["id"]
        )
    b_id = client.post(
        "/api/generate",
        headers=h,
        json={"account_id": b["id"], "prompt": "Art direction for B"},
    ).json()["id"]
    import threading

    release = threading.Event()
    started = []
    mutex = threading.Lock()
    monkeypatch.setattr(m, "process_state", lambda acc: "running")
    monkeypatch.setattr(m, "upstream_probe", lambda acc: True)

    def fake_exec(jid, acc, prompt, aspect, q, n):
        with mutex:
            started.append((jid, acc["id"]))
        release.wait(3)
        with m.engine.begin() as c:
            c.execute(
                m.update(m.jobs)
                .where(m.jobs.c.id == jid)
                .values(status="completed", finished_at=m.now())
            )
        with m.queue_guard:
            m.active_jobs.discard(jid)
            m.active_per_account[acc["id"]] -= 1
            m.queue_wakeup.set()

    monkeypatch.setattr(m, "execute_job", fake_exec)
    try:
        m.queue_wakeup.set()
        for _ in range(120):
            with mutex:
                n = len(started)
                seen = {x[1] for x in started}
            if n == 3:
                break
            time.sleep(0.02)
        assert n == 3, started
        assert b["id"] in seen, "Lighter account must not starve behind heavy account"
        assert sum(1 for x in started if x[1] == a["id"]) <= 2
        assert sum(1 for x in started if x[1] == b["id"]) <= 2
        assert client.get("/api/queue").json()["running"] == 3
    finally:
        release.set()
    for _ in range(200):
        if (
            client.get("/api/queue").json()["queued"] == 0
            and client.get("/api/queue").json()["running"] == 0
        ):
            break
        time.sleep(0.02)
    assert client.get("/api/queue").json()["queued"] == 0
    assert client.get("/api/queue").json()["running"] == 0


def test_restart_marks_running_unknown_preserves_queued(tmp_path, monkeypatch):
    """Hard restart must not retry work that might already have run upstream."""
    import sqlite3, uuid

    db = tmp_path / "legacy-unknown.db"
    con = sqlite3.connect(db)
    con.executescript(
        """CREATE TABLE accounts(id VARCHAR(36) PRIMARY KEY,name VARCHAR(80) NOT NULL,email_label VARCHAR(160),port INTEGER,api_secret VARCHAR(100),autostart INTEGER,archived_at VARCHAR(40),plan_label VARCHAR(16),plan_checked_at VARCHAR(40),created_at VARCHAR(40));
    CREATE TABLE jobs(id VARCHAR(36) PRIMARY KEY,account_id VARCHAR(36),prompt TEXT,aspect_ratio VARCHAR(8),quality_preference VARCHAR(16),image_width INTEGER,image_height INTEGER,image_format VARCHAR(12),image_bytes INTEGER,image_sha256 VARCHAR(64),status VARCHAR(20),error TEXT,image_file VARCHAR(120),created_at VARCHAR(40),started_at VARCHAR(40),finished_at VARCHAR(40));"""
    )
    aid = str(uuid.uuid4())
    con.execute(
        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, "Older", "", 41001, "secret", 0, None, "unknown", None, "2026-01-01"),
    )
    for status in ("running", "queued"):
        con.execute(
            "INSERT INTO jobs (id,account_id,prompt,aspect_ratio,quality_preference,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), aid, "A scene", "1:1", "best", status, "2026-01-01"),
        )
    con.commit()
    con.close()
    monkeypatch.setenv("ADMIN_PASSWORD", "example-passphrase-123456789")
    monkeypatch.setenv("APP_SECRET", "a" * 64)
    monkeypatch.setenv("AGENT_API_KEY", "test-agent-key-98765")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "legacydata"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db))
    import importlib, sys

    sys.modules.pop("app", None)
    m = importlib.import_module("app").app.state.hub
    from sqlalchemy import select

    m.select = select
    with m.engine.connect() as conn:
        statuses = set(conn.execute(m.select(m.jobs.c.status)).scalars())
    assert statuses == {"unknown", "queued"}
    m.engine.dispose()
    sys.modules.pop("app", None)


def test_end_to_end_three_image_response_via_http_mock(loaded, monkeypatch):
    """Proves Hub -> HTTP gateway -> three original outputs -> DB/gallery/API."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from threading import Thread
    import json, time

    client, m = loaded
    h = auth(client)
    png = io.BytesIO()
    Image.new("RGB", (31, 21), "orange").save(png, format="PNG")
    content = base64.b64encode(png.getvalue()).decode()
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("content-length", "0")))
            )
            seen.append(body)
            payload = json.dumps(
                {"data": [{"b64_json": content} for _ in range(body["n"])]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(m, "process_state", lambda acc: "running")
    monkeypatch.setattr(m, "upstream_probe", lambda acc: True)
    monkeypatch.setattr(
        m, "upstream", lambda acc: f"http://127.0.0.1:{server.server_port}"
    )
    try:
        acc = client.post(
            "/api/accounts", headers=h, json={"name": "Mock N3", "autostart": False}
        ).json()
        r = client.post(
            "/api/generate",
            headers=h,
            json={"account_id": acc["id"], "n": 3, "prompt": "three oranges"},
        )
        assert r.status_code == 202, r.text
        jid = r.json()["id"]
        for _ in range(200):
            j = client.get("/api/jobs/" + jid).json()
            if j["status"] in ("completed", "failed", "partial"):
                break
            time.sleep(0.03)
        assert j["status"] == "completed", j
        assert len(seen) == 1 and seen[0]["n"] == 3
        assert len(j["images"]) == 3
        for i in range(3):
            assert client.get(f"/api/images/{jid}/{i}").content == png.getvalue()
    finally:
        server.shutdown()
        server.server_close()


def test_provider_429_cools_account_without_rotation(loaded, monkeypatch):
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from threading import Thread
    import time

    client, m = loaded
    h = auth(client)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            # Drain the request body and send a close-delimited-free reply: on Windows a
            # server that closes with unread request data resets the connection, which
            # looked like a lost upstream response instead of the simulated 429.
            self.rfile.read(int(self.headers.get("content-length", "0") or 0))
            body = b'{"error":{"error_kind":"rate_limit","message":"Test simulated rate-limit"}}'
            seen.append(self.path)
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(m, "process_state", lambda acc: "running")
    monkeypatch.setattr(m, "upstream_probe", lambda acc: True)
    monkeypatch.setattr(
        m, "upstream", lambda acc: f"http://127.0.0.1:{server.server_port}"
    )
    try:
        acc = client.post(
            "/api/accounts",
            headers=h,
            json={"name": "Limited", "parallel_limit": 1, "autostart": False},
        ).json()
        first = client.post(
            "/api/generate",
            headers=h,
            json={"account_id": acc["id"], "prompt": "first test image"},
        ).json()["id"]
        second = client.post(
            "/api/generate",
            headers=h,
            json={"account_id": acc["id"], "prompt": "second test image"},
        ).json()["id"]
        for _ in range(200):
            result = client.get("/api/jobs/" + first).json()
            if result["status"] == "rate_limited":
                break
            time.sleep(0.03)
        assert result["status"] == "rate_limited", result
        time.sleep(0.15)
        assert client.get("/api/jobs/" + second).json()["status"] == "queued"
        assert len(seen) == 1, seen
        a = client.get("/api/accounts").json()[0]
        assert a["cooldown_until"] is not None
    finally:
        server.shutdown()
        server.server_close()


def test_five_hundred_pending_jobs_do_not_consume_worker_threads(loaded):
    """Exercise target queue size only; never claims 500 images can be rendered in 5 h."""
    client, m = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts",
        headers=h,
        json={"name": "500 pending test", "autostart": False},
    ).json()
    for i in range(500):
        result = m.queue_image(
            m.GenerateBody(account_id=acc["id"], prompt=f"Creative asset number {i}")
        )
        assert result["status"] == "queued"
    summary = client.get("/api/queue").json()
    assert summary["queued"] == 500 and summary["running"] == 0
    assert m.active_jobs == set()
    assert client.get("/api/jobs?limit=200").status_code == 200


# v0.4: control plane / analytics / operational safety


def test_control_requires_auth_csrf_and_validates(loaded):
    client, mod = loaded
    assert client.get("/api/control").status_code == 401
    assert client.get("/api/analytics?hours=5").status_code == 401
    h = auth(client)
    assert (
        client.patch("/api/control", json={"global_max_inflight": 3}).status_code == 403
    )
    assert (
        client.patch(
            "/api/control", headers=h, json={"global_max_inflight": 0}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/control", headers=h, json={"global_max_inflight": 33}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/control", headers=h, json={"max_batch_images": 11}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/control", headers=h, json={"dispatch_paused": None}
        ).status_code
        == 422
    )
    assert client.get("/api/analytics?hours=2").status_code == 422


def test_control_updates_persist_and_are_audited(loaded):
    client, mod = loaded
    h = auth(client)
    initial = client.get("/api/control").json()["config"]
    assert initial["global_max_inflight"] == 4
    res = client.patch(
        "/api/control",
        headers=h,
        json={
            "global_max_inflight": 5,
            "gateway_max_tabs": 4,
            "max_pending_jobs": 250,
            "max_batch_images": 7,
        },
    )
    assert res.status_code == 200, res.text
    assert "gateway_max_tabs" in res.json()["restart_required"]
    assert "max_batch_images" in res.json()["restart_required"]
    assert client.get("/api/control").json()["config"]["max_pending_jobs"] == 250
    assert client.get("/api/settings").json()["global_max_inflight"] == 5
    assert client.get("/api/queue").json()["max_global_inflight"] == 5
    audit = client.get("/api/control/audit").json()
    assert audit[0]["action"] == "config.update"
    with mod.engine.connect() as conn:
        saved = dict(
            conn.execute(
                mod.select(mod.control_settings.c.key, mod.control_settings.c.value)
            ).all()
        )
    assert saved["max_pending_jobs"] == "250"


def test_dispatch_paused_preserves_waiting_job(loaded):
    client, mod = loaded
    h = auth(client)
    aid = client.post(
        "/api/accounts", headers=h, json={"name": "Pause target", "autostart": False}
    ).json()["id"]
    r = client.patch("/api/control", headers=h, json={"dispatch_paused": True})
    assert r.status_code == 200
    j = client.post(
        "/api/generate",
        headers=h,
        json={"account_id": aid, "prompt": "a valid artwork", "n": 2},
    ).json()
    assert mod._dispatch_available() == 0
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "queued"
    assert client.get("/api/queue").json()["dispatch_paused"] is True
    assert (
        client.patch(
            "/api/control", headers=h, json={"dispatch_paused": False}
        ).status_code
        == 200
    )
    assert client.get("/api/jobs/" + j["id"]).json()["status"] == "queued"


def test_analytics_counts_outputs_not_jobs(loaded):
    import uuid

    client, mod = loaded
    h = auth(client)
    a = client.post(
        "/api/accounts", headers=h, json={"name": "Metrics A", "autostart": False}
    ).json()
    b = client.post(
        "/api/accounts", headers=h, json={"name": "Metrics B", "autostart": False}
    ).json()
    when = mod.now()
    jid = str(uuid.uuid4())
    jid2 = str(uuid.uuid4())
    with mod.engine.begin() as conn:
        conn.execute(
            mod.jobs.insert().values(
                id=jid,
                account_id=a["id"],
                prompt="portrait",
                aspect_ratio="1:1",
                quality_preference="best",
                n=3,
                status="completed",
                created_at=when,
                started_at=when,
                finished_at=when,
            )
        )
        conn.execute(
            mod.jobs.insert().values(
                id=jid2,
                account_id=b["id"],
                prompt="abstract",
                aspect_ratio="1:1",
                quality_preference="best",
                n=2,
                status="partial",
                created_at=when,
                started_at=when,
                finished_at=when,
            )
        )
        for ix in range(3):
            conn.execute(
                mod.job_images.insert().values(
                    id=str(uuid.uuid4()),
                    job_id=jid,
                    position=ix,
                    image_file=f"fake-{ix}.png",
                )
            )
        conn.execute(
            mod.job_images.insert().values(
                id=str(uuid.uuid4()), job_id=jid2, position=0, image_file="fake-b.png"
            )
        )
    metrics = client.get("/api/analytics?hours=5").json()
    assert metrics["images"] == 4
    assert metrics["requested_images"] == 5
    assert metrics["jobs"] == 2
    assert metrics["status_counts"] == {"completed": 1, "partial": 1}
    assert metrics["throughput_images_per_hour"] == 0.8
    assert metrics["target_window_projection"] == 4.0
    assert sum(x["images"] for x in metrics["series"]) == 4
    assert {r["name"]: r["images"] for r in metrics["accounts"]} == {
        "Metrics A": 3,
        "Metrics B": 1,
    }
    assert metrics["p95_sec"] == 0


def test_batch_queue_ceiling_updates_without_restart(loaded):
    client, mod = loaded
    h = auth(client)
    aid = client.post(
        "/api/accounts", headers=h, json={"name": "Batch limits", "autostart": False}
    ).json()["id"]
    assert (
        client.patch(
            "/api/control",
            headers=h,
            json={"max_batch_images": 2, "max_pending_jobs": 1},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/generate",
            headers=h,
            json={"account_id": aid, "prompt": "A bird in sky", "n": 3},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/generate",
            headers=h,
            json={"account_id": aid, "prompt": "A bird in sky", "n": 2},
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/generate",
            headers=h,
            json={"account_id": aid, "prompt": "A second bird", "n": 1},
        ).status_code
        == 429
    )


def test_static_dashboard_features_exist(loaded):
    client, mod = loaded
    h = auth(client)
    html = client.get("/").text
    js = client.get("/static/app.js").text
    assert 'data-page="operations"' in html
    assert 'data-page="analytics"' in html
    assert 'id="controlForm"' in html and 'id="analyticsChart"' in html
    assert 'data-hours="720"' in html
    assert "/api/control" in js and "/api/analytics?hours=" in js


def test_account_dispatch_pause_and_resume_does_not_clear_cooldown(loaded):
    client, mod = loaded
    h = auth(client)
    acc = client.post(
        "/api/accounts", headers=h, json={"name": "Paused account", "autostart": False}
    ).json()
    with mod.engine.begin() as c:
        c.execute(
            mod.update(mod.accounts)
            .where(mod.accounts.c.id == acc["id"])
            .values(cooldown_until="2100-01-01T00:00:00+00:00")
        )
    r = client.post("/api/accounts/" + acc["id"] + "/dispatch/pause", headers=h)
    assert r.status_code == 200 and r.json()["dispatch_enabled"] is False
    assert client.get("/api/accounts").json()[0]["dispatch_enabled"] == 0
    assert (
        client.post(
            "/api/generate",
            headers=h,
            json={"account_id": acc["id"], "prompt": "A calm forest"},
        ).status_code
        == 202
    )
    assert mod._dispatch_available() == 0
    r = client.post("/api/accounts/" + acc["id"] + "/dispatch/resume", headers=h)
    assert r.status_code == 200 and r.json()["dispatch_enabled"] is True
    assert client.get("/api/accounts").json()[0]["cooldown_until"].startswith("2100-")
    audit = client.get("/api/control/audit").json()
    assert [x["action"] for x in audit[:2]] == ["account.dispatch", "account.dispatch"]


def test_target_is_editable_from_panel_without_changing_provider_limit(loaded):
    client, m = loaded
    h = auth(client)
    result = client.patch(
        "/api/control", headers=h, json={"target_images": 120, "target_window_hours": 3}
    )
    assert result.status_code == 200
    report = client.get("/api/analytics?hours=5").json()
    assert report["target_images"] == 120 and report["target_window_hours"] == 3
    assert report["target_rate_per_hour"] == 40
    assert report["target_window_projection"] == 0
    assert (
        client.patch(
            "/api/control", headers=h, json={"target_window_hours": 0}
        ).status_code
        == 422
    )


def test_gallery_output_timestamp_persisted(loaded):
    import uuid

    client, m = loaded
    h = auth(client)
    aid = client.post(
        "/api/accounts", headers=h, json={"name": "Timestamped", "autostart": False}
    ).json()["id"]
    jid = str(uuid.uuid4())
    with m.engine.begin() as conn:
        conn.execute(
            m.jobs.insert().values(
                id=jid,
                account_id=aid,
                prompt="photograph",
                aspect_ratio="1:1",
                quality_preference="best",
                n=1,
                status="running",
                created_at=m.now(),
            )
        )
    png = io.BytesIO()
    Image.new("RGB", (5, 6), "red").save(png, format="PNG")
    stored, error = m.record_image_batch(
        {"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]},
        jid,
        41000,
        1,
    )
    assert not error and len(stored) == 1
    with m.engine.connect() as conn:
        row = (
            conn.execute(m.select(m.job_images).where(m.job_images.c.job_id == jid))
            .mappings()
            .first()
        )
    assert row["stored_at"] and row["image_bytes"] == len(png.getvalue())


def test_analytics_older_created_newly_finished_job(loaded):
    import uuid
    from datetime import datetime, timezone, timedelta

    client, m = loaded
    h = auth(client)
    aid = client.post(
        "/api/accounts", headers=h, json={"name": "Cross window", "autostart": False}
    ).json()["id"]
    jid = str(uuid.uuid4())
    created = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    finished = m.now()
    with m.engine.begin() as conn:
        conn.execute(
            m.jobs.insert().values(
                id=jid,
                account_id=aid,
                prompt="old queue",
                aspect_ratio="1:1",
                quality_preference="best",
                n=1,
                status="completed",
                created_at=created,
                started_at=finished,
                finished_at=finished,
            )
        )
        conn.execute(
            m.job_images.insert().values(
                id=str(uuid.uuid4()),
                job_id=jid,
                position=0,
                image_file="mock.png",
                stored_at=finished,
            )
        )
    data = client.get("/api/analytics?hours=5").json()
    assert data["images"] == 1 and data["jobs"] == 1
    assert sum(x["images"] for x in data["series"]) == 1
