"""Fault-injection tests for the v0.5 transport and delivery boundary."""

import base64
import hashlib
import io
import uuid
import zipfile

import pytest
from PIL import Image
from sqlalchemy import select

from test_app import loaded, auth
from hub import execution, gateway, media


def png():
    stream = io.BytesIO()
    Image.new("RGB", (80, 60), "#278c76").save(stream, "PNG")
    return stream.getvalue()


def saved_job(client, ctx, headers, count=2):
    account = client.post(
        "/api/accounts", json={"name": "Delivery test"}, headers=headers
    ).json()
    jid = client.post(
        "/api/generate",
        json={"account_id": account["id"], "prompt": "Delivery fixture", "n": count},
        headers=headers,
    ).json()["id"]
    raw = png()
    stored, error = ctx.record_image_batch(
        {"data": [{"b64_json": base64.b64encode(raw).decode()}] * count},
        jid,
        41000,
        count,
    )
    assert not error
    with ctx.engine.begin() as conn:
        conn.execute(
            ctx.jobs.update().where(ctx.jobs.c.id == jid).values(status="completed")
        )
    return jid, raw, stored


def test_original_range_preview_and_zip(loaded):
    client, ctx = loaded
    h = auth(client)
    jid, raw, stored = saved_job(client, ctx, h)
    response = client.get(f"/api/images/{jid}/0", headers={"Range": "bytes=0-15"})
    assert response.status_code == 206 and response.content == raw[:16]
    preview = client.get(f"/api/previews/{jid}/0")
    assert (
        preview.status_code == 200 and preview.headers["content-type"] == "image/webp"
    )
    assert (ctx.DATA / "gallery" / stored[0][0]).read_bytes() == raw
    response = client.get(f"/api/exports/jobs/{jid}.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert all(archive.read(name) == raw for name in archive.namelist())
    assert not list((ctx.DATA / "exports").glob("*.zip"))
    assert client.get(f"/v1/exports/jobs/{jid}.zip").status_code == 401
    assert (
        client.get(
            f"/v1/exports/jobs/{jid}.zip",
            headers={"Authorization": "Bearer test-agent-key-98765"},
        ).status_code
        == 200
    )
    client.post("/api/auth/logout", headers=h)
    assert client.get(f"/api/previews/{jid}/0").status_code == 401
    assert client.get("/api/gallery").status_code == 401
    assert client.get(f"/api/exports/jobs/{jid}.zip").status_code == 401


def test_gallery_keyset_has_no_duplicates_and_covers_all_outputs(loaded):
    client, ctx = loaded
    h = auth(client)
    for _ in range(3):
        saved_job(client, ctx, h, count=2)
    found = []
    cursor = None
    while True:
        response = client.get(
            "/api/gallery",
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        page = response.json()
        found.extend((r["job_id"], r["position"]) for r in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(set(found)) == len(found) == 6
    assert client.get("/api/gallery?cursor=invalid").status_code == 422
    assert client.get("/api/gallery?limit=1000").status_code == 422


def test_oversized_base64_rejected_before_decode_and_no_partial_files(
    loaded, monkeypatch
):
    _, ctx = loaded
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 16)
    with pytest.raises(ValueError, match="too large"):
        ctx.write_image_payload(
            {"data": [{"b64_json": "A" * 1000}]}, str(uuid.uuid4()), 41000
        )
    assert not list((ctx.DATA / "gallery").iterdir())


def test_stream_cap_checks_real_bytes_and_cleans_temporary(loaded, monkeypatch):
    _, ctx = loaded
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 16)
    monkeypatch.setattr(
        media, "image_chunks", lambda *args: iter([b"x" * 10, b"y" * 10])
    )
    with pytest.raises(ValueError, match="too large"):
        media.store_original(ctx.DATA / "gallery", str(uuid.uuid4()), {}, 41000)
    assert not list((ctx.DATA / "gallery").iterdir())


def test_atomic_no_clobber_and_original_hash(loaded):
    _, ctx = loaded
    raw = png()
    jid = str(uuid.uuid4())
    payload = {"data": [{"b64_json": base64.b64encode(raw).decode()}]}
    name, info = ctx.write_image_payload(payload, jid, 41000)
    with pytest.raises(FileExistsError):
        ctx.write_image_payload(payload, jid, 41000)
    assert info["image_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (ctx.DATA / "gallery" / name).read_bytes() == raw
    assert not list((ctx.DATA / "gallery").glob(".incoming-*"))


def test_database_failure_removes_unindexed_original(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    account = client.post("/api/accounts", json={"name": "Rollback"}, headers=h).json()
    jid = client.post(
        "/api/generate",
        json={"account_id": account["id"], "prompt": "Failure fixture"},
        headers=h,
    ).json()["id"]
    original_begin = ctx.engine.begin
    from contextlib import contextmanager

    @contextmanager
    def broken():
        raise RuntimeError("injected DB failure")
        yield

    monkeypatch.setattr(ctx.engine, "begin", broken)
    stored, error = ctx.record_image_batch(
        {"data": [{"b64_json": base64.b64encode(png()).decode()}]}, jid, 41000, 1
    )
    assert stored == [] and "injected DB failure" in error
    assert not list((ctx.DATA / "gallery").iterdir())
    monkeypatch.setattr(ctx.engine, "begin", original_begin)


def test_rejects_symlink_escape(loaded, tmp_path):
    client, ctx = loaded
    h = auth(client)
    jid, raw, stored = saved_job(client, ctx, h, 1)
    path = ctx.DATA / "gallery" / stored[0][0]
    path.unlink()
    secret = tmp_path / "secret.png"
    secret.write_bytes(raw)
    try:
        path.symlink_to(secret)
    except OSError as exc:
        # Windows without Developer Mode / SeCreateSymbolicLinkPrivilege raises 1314.
        pytest.skip(f"symlinks are unavailable on this host: {exc}")
    assert client.get(f"/api/images/{jid}").status_code == 404


def test_gateway_url_port_credentials_and_redirect_guard():
    for url in [
        "http://127.0.0.1:41001/images/a.png",
        "https://127.0.0.1:41000/images/a.png",
        "http://user:pass@127.0.0.1:41000/images/a.png",
        "http://127.0.0.1:41000/images/a.png?q=1",
    ]:
        with pytest.raises(ValueError):
            media.approved_gateway_url(url, 41000)


def test_factory_keeps_application_state_isolated(loaded):
    client, ctx = loaded
    from hub import create_app

    second = create_app()
    other = second.state.hub
    assert other is not ctx and other.queue_guard is not ctx.queue_guard
    assert other.config_cache is not ctx.config_cache and other.pool is not ctx.pool
    other.pool.shutdown()
    other.status_pool.shutdown()
    other.engine.dispose()


def test_redirect_is_not_followed(loaded, monkeypatch):
    _, ctx = loaded

    class FakeResponse:
        status_code = 302
        headers = {}

        def raise_for_status(self):
            pass

        def iter_bytes(self, *args):
            raise AssertionError("redirect body must never be consumed")

    from contextlib import contextmanager

    @contextmanager
    def redirect(*args, **kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        yield FakeResponse()

    monkeypatch.setattr(media.httpx, "stream", redirect)
    with pytest.raises(ValueError, match="redirect"):
        ctx.write_image_payload(
            {"data": [{"url": "http://127.0.0.1:41000/images/a.png"}]},
            str(uuid.uuid4()),
            41000,
        )
    assert not list((ctx.DATA / "gallery").iterdir())


def test_json_budget_failure_remains_unknown_without_replay(loaded, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    # Stop the background scheduler BEFORE queueing: this test executes the job by hand
    # and must not race a real dispatch (which would call request_generation twice).
    ctx.stop_scheduler()
    account = client.post(
        "/api/accounts", json={"name": "Budget test"}, headers=h
    ).json()
    jid = client.post(
        "/api/generate",
        json={"account_id": account["id"], "prompt": "Budget fixture"},
        headers=h,
    ).json()["id"]
    monkeypatch.setattr(ctx, "process_state", lambda a: "running")
    monkeypatch.setattr(ctx, "upstream_probe", lambda a: True)
    calls = []

    def reject(*args):
        calls.append(1)
        raise gateway.ResponseBudgetError("injected response budget")

    monkeypatch.setattr(execution, "request_generation", reject)
    ctx.execute_job(
        jid, ctx.query_account(account["id"]), "Budget fixture", "1:1", "balanced", 1
    )
    assert client.get("/api/jobs/" + jid).json()["status"] == "unknown"
    assert calls == [1]
    assert not list((ctx.DATA / "gallery").iterdir())


def test_queue_candidate_window_does_not_starve_later_account_when_cap_lowered(loaded):
    from hub.schemas import GenerateBody

    client, ctx = loaded
    h = auth(client)
    ctx.stop_scheduler()
    a = client.post("/api/accounts", json={"name": "Long queue"}, headers=h).json()
    b = client.post("/api/accounts", json={"name": "Later queue"}, headers=h).json()
    for i in range(10):
        ctx.queue_image(GenerateBody(account_id=a["id"], prompt=f"Queued asset {i}"))
    ctx.queue_image(GenerateBody(account_id=b["id"], prompt="Later account asset"))
    client.patch("/api/control", json={"max_pending_jobs": 1}, headers=h)
    candidates, _ = ctx._candidate_jobs()
    assert b["id"] in candidates
    assert len(candidates[a["id"]]) <= ctx.setting("global_max_inflight")


def test_frontend_is_native_modules_and_demo_is_not_loaded_in_live_app(loaded):
    client, ctx = loaded
    html = client.get("/").text
    assert 'type="module"' in html
    assert "demo.js" not in html
    assert client.get("/static/modules/controller.js").status_code == 200


def test_large_control_body_rejected_before_json_parser(loaded):
    client, ctx = loaded
    response = client.post(
        "/api/auth/login",
        content=b'{"password":"' + b"x" * 70000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_gateway_url_stream_preserves_original_and_rejects_oversize_header(
    loaded, monkeypatch
):
    from contextlib import contextmanager

    _, ctx = loaded
    raw = png()
    state = {"large": False}

    class FakeResponse:
        status_code = 200

        @property
        def headers(self):
            return {
                "content-length": str(
                    media.MAX_IMAGE_BYTES + 1 if state["large"] else len(raw)
                )
            }

        def raise_for_status(self):
            pass

        def iter_bytes(self, size):
            assert size == media.CHUNK_BYTES
            yield raw[:30]
            yield raw[30:]

    @contextmanager
    def stream(*args, **kwargs):
        yield FakeResponse()

    monkeypatch.setattr(media.httpx, "stream", stream)
    payload = {"data": [{"url": "http://127.0.0.1:41000/images/sample.png"}]}
    name, _ = ctx.write_image_payload(payload, str(uuid.uuid4()), 41000)
    assert (ctx.DATA / "gallery" / name).read_bytes() == raw
    state["large"] = True
    with pytest.raises(ValueError, match="too large"):
        ctx.write_image_payload(payload, str(uuid.uuid4()), 41000)
    assert not list((ctx.DATA / "gallery").glob(".incoming-*"))


def test_gateway_server_failure_after_submission_is_unknown(loaded, monkeypatch):
    from types import SimpleNamespace
    from hub import scheduler

    client, ctx = loaded
    h = auth(client)
    ctx.stop_scheduler()
    a = client.post("/api/accounts", json={"name": "Server failure"}, headers=h).json()
    jid = client.post(
        "/api/generate",
        json={"account_id": a["id"], "prompt": "Failure fixture"},
        headers=h,
    ).json()["id"]
    monkeypatch.setattr(ctx, "process_state", lambda a: "running")
    monkeypatch.setattr(ctx, "upstream_probe", lambda a: True)
    monkeypatch.setattr(
        execution,
        "request_generation",
        lambda *args: SimpleNamespace(
            status_code=502, data={"error": "bad gateway"}, headers={}
        ),
    )
    ctx.execute_job(
        jid, ctx.query_account(a["id"]), "Failure fixture", "1:1", "balanced", 1
    )
    job = client.get("/api/jobs/" + jid).json()
    assert job["status"] == "unknown" and "before retry" in job["error"]
