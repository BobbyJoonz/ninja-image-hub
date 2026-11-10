"""Operator tooling: doctor patch checks, resolve_unknown filters, resolve-release."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from helpers import auth


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_mod", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _workspace(data, account_id, marker, wait=None):
    gateway = Path(data) / "accounts" / account_id / "gateway"
    (gateway / "src").mkdir(parents=True)
    (gateway / "src" / "chatgpt.js").write_text(marker, encoding="utf-8")
    return gateway


def test_doctor_flags_missing_gateway_patch(tmp_path):
    doctor = _load("doctor")
    data = tmp_path / "data"
    _workspace(data, "aaaaaaaa-0000-0000-0000-000000000001", "// pristine upstream\n")
    workspaces, issues = doctor.worker_patch_issues(data, {}, None)
    assert workspaces == 1
    assert [i["kind"] for i in issues] == ["patch_missing"]

    _workspace(data, "bbbbbbbb-0000-0000-0000-000000000002", "// ninja:fast-prompt-insert\n")
    workspaces, issues = doctor.worker_patch_issues(data, {}, None)
    assert workspaces == 2
    # The unpatched workspace is still reported; patched one is not.
    assert len(issues) == 1 and issues[0]["account"].startswith("aaaaaaaa")


def test_doctor_flags_render_wait_mismatch(tmp_path):
    doctor = _load("doctor")
    data = tmp_path / "data"
    _workspace(data, "cccccccc-0000-0000-0000-000000000003", "// ninja:fast-prompt-insert\n")
    _, issues = doctor.worker_patch_issues(
        data, {41001: {"PORT": "41001", "RENDER_WAIT_SEC": "15"}}, 8
    )
    assert any(i["kind"] == "render_wait_mismatch" for i in issues)
    _, issues = doctor.worker_patch_issues(data, {41001: {"PORT": "41001"}}, 8)
    assert any(i["kind"] == "render_wait_missing" for i in issues)
    _, issues = doctor.worker_patch_issues(
        data, {41001: {"PORT": "41001", "RENDER_WAIT_SEC": "8"}}, 8
    )
    assert not issues


def test_resolve_unknown_filtering():
    import time

    resolver = _load("resolve_unknown")
    now = time.time()
    old_stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 7200))
    fresh_stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 60))
    jobs = [
        {"id": "a", "status": "unknown", "error": "Gateway page_load_failed", "created_at": old_stamp},
        {"id": "b", "status": "unknown", "resolution": "abandoned", "error": "x"},
        {"id": "c", "status": "failed", "error": "page_load_failed"},
        {"id": "d", "status": "unknown", "error": "timeout", "created_at": fresh_stamp},
    ]
    assert [j["id"] for j in resolver.filter_unknown(jobs)] == ["a", "d"]
    assert [j["id"] for j in resolver.filter_unknown(jobs, match="page_load")] == ["a"]
    assert [j["id"] for j in resolver.filter_unknown(jobs, ids=["d"])] == ["d"]
    assert [j["id"] for j in resolver.filter_unknown(jobs, match="nothing")] == []
    # Age filter: only the two-hour-old job survives a one-hour threshold.
    assert [j["id"] for j in resolver.filter_unknown(jobs, older_than_hours=1, now=now)] == ["a"]


def test_resolving_unknown_releases_reference_workspace(loaded, tmp_path, monkeypatch):
    client, ctx = loaded
    h = auth(client)
    # Build an unknown job with a reference upload, using the real upload API.
    acc = client.post("/api/accounts", json={"name": "Resolve refs", "autostart": False}, headers=h).json()
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(buffer, format="PNG")
    upload = client.post(
        "/api/uploads",
        files=[("files", ("ref.png", buffer.getvalue(), "image/png"))],
        headers=h,
    ).json()
    client.patch("/api/control", json={"dispatch_paused": True}, headers=h)
    job = client.post(
        "/api/generate",
        json={
            "account_id": acc["id"],
            "prompt": "unknown to resolve",
            "upload_id": upload["upload_id"],
            "idempotency_key": "resolve-refs-0001",
        },
        headers=h,
    ).json()
    # Mark it unknown as a lost-response would.
    from sqlalchemy import update

    with ctx.engine.begin() as conn:
        conn.execute(
            update(ctx.jobs)
            .where(ctx.jobs.c.id == job["id"])
            .values(status="unknown", error="Gateway timeout; outcome uncertain.")
        )
    assert (ctx.DATA / "uploads" / upload["upload_id"]).exists()
    resolved = client.post(
        f"/api/jobs/{job['id']}/resolve",
        json={"resolution": "not_delivered", "note": "checked chat"},
        headers=h,
    )
    assert resolved.status_code == 200, resolved.text
    assert not (ctx.DATA / "uploads" / upload["upload_id"]).exists()
