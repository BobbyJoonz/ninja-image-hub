import re
import threading
import uuid
from collections import defaultdict
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from .schemas import GenerateBody, ResolveBody


def queue_image(ctx, body: GenerateBody):
    if body.aspect_ratio not in {"1:1", "16:9", "9:16", "4:3", "3:4"}:
        raise HTTPException(422, "Unsupported aspect ratio.")
    if body.quality_preference not in {"draft", "balanced", "best"}:
        raise HTTPException(422, "Unsupported quality preference.")
    if body.n > ctx.setting("max_batch_images"):
        raise HTTPException(
            422,
            f"Maximum images per request in this installation: {ctx.setting('max_batch_images')}.",
        )
    if body.idempotency_key and (
        not re.fullmatch("[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", body.idempotency_key)
    ):
        raise HTTPException(422, "Invalid idempotency key (8..128 safe characters).")
    upload = None
    if body.upload_id:
        upload = ctx.load_upload(body.upload_id)
        if not upload:
            raise HTTPException(
                422,
                "Reference upload not found or expired. Upload the images again.",
            )
    with ctx.account_mutation_locks.setdefault(body.account_id, threading.RLock()):
        with ctx.queue_guard:
            acc = ctx.query_account(body.account_id)
            if not acc:
                raise HTTPException(404, "Account not found.")
            if acc["archived_at"]:
                raise HTTPException(409, "Account archived; restore before generating.")
            with ctx.engine.begin() as conn:
                if body.idempotency_key:
                    existing = (
                        conn.execute(
                            select(ctx.jobs).where(
                                ctx.jobs.c.account_id == acc["id"],
                                ctx.jobs.c.idempotency_key == body.idempotency_key,
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if existing:
                        if any(
                            (
                                existing["prompt"] != body.prompt,
                                existing["aspect_ratio"] != body.aspect_ratio,
                                existing["quality_preference"]
                                != body.quality_preference,
                                (existing["n"] or 1) != body.n,
                                (existing["reference_upload_id"] or None)
                                != (body.upload_id or None),
                            )
                        ):
                            raise HTTPException(
                                409,
                                "Idempotency key reused with different image request.",
                            )
                        return {
                            "id": existing["id"],
                            "status": existing["status"],
                            "n": existing["n"],
                            "deduplicated": True,
                        }
                pending = conn.execute(
                    select(func.count())
                    .select_from(ctx.jobs)
                    .where(ctx.jobs.c.status == "queued")
                ).scalar_one()
                if pending >= ctx.setting("max_pending_jobs"):
                    raise HTTPException(
                        429,
                        "Queue full; wait for existing jobs. Retry later without changing the idempotency key.",
                    )
                jid = str(uuid.uuid4())
                try:
                    conn.execute(
                        ctx.jobs.insert().values(
                            id=jid,
                            account_id=acc["id"],
                            prompt=body.prompt,
                            aspect_ratio=body.aspect_ratio,
                            quality_preference=body.quality_preference,
                            n=body.n,
                            idempotency_key=body.idempotency_key,
                            reference_upload_id=body.upload_id,
                            reference_count=int((upload or {}).get("count") or 0)
                            or None,
                            status="queued",
                            created_at=ctx.now(),
                        )
                    )
                except IntegrityError:
                    raise HTTPException(
                        409,
                        "Concurrent idempotency conflict. Request the job with the original key.",
                    )
            ctx.queue_wakeup.set()
    return {"id": jid, "status": "queued", "n": body.n, "deduplicated": False}


def generate(ctx, body: GenerateBody):
    return ctx.queue_image(body)


def agent_generate(ctx, body: GenerateBody):
    return ctx.queue_image(body)


def serialize_job(ctx, row, agent=False, image_rows=None):
    if image_rows is None:
        with ctx.engine.connect() as conn:
            image_rows = (
                conn.execute(
                    select(ctx.job_images)
                    .where(ctx.job_images.c.job_id == row["id"])
                    .order_by(ctx.job_images.c.position)
                )
                .mappings()
                .all()
            )
    prefix = "/v1" if agent else "/api"
    outputs = [
        {
            "position": r["position"],
            "preview_url": (
                f"/api/previews/{row['id']}/{r['position']}" if not agent else None
            ),
            "url": f"{prefix}/images/{row['id']}/{r['position']}",
            **{
                k: r[k]
                for k in (
                    "image_width",
                    "image_height",
                    "image_format",
                    "image_bytes",
                    "image_sha256",
                )
            },
        }
        for r in image_rows
    ]
    if not outputs and row.get("image_file"):
        outputs = [
            {
                "position": 0,
                "url": f"{prefix}/images/{row['id']}/0",
                **{
                    k: row.get(k)
                    for k in (
                        "image_width",
                        "image_height",
                        "image_format",
                        "image_bytes",
                        "image_sha256",
                    )
                },
            }
        ]
    return {
        k: row.get(k)
        for k in [
            "id",
            "account_id",
            "prompt",
            "aspect_ratio",
            "quality_preference",
            "status",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "n",
            "image_width",
            "image_height",
            "image_format",
            "image_bytes",
            "image_sha256",
            "resolution",
            "resolved_at",
            "resolution_note",
            "thinking_requested",
            "thinking_applied",
            "thinking_verified",
            "thinking_fallback",
            "thinking_mixed",
            "reference_upload_id",
            "reference_count",
        ]
    } | {
        "images": outputs,
        "image_count": len(outputs),
        "image_url": outputs[0]["url"] if outputs else None,
    }


def unresolved_unknown(ctx):
    """Jobs whose upstream outcome is unknown and that nobody has reconciled yet."""
    with ctx.engine.connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(
                ctx.jobs.c.status == "unknown",
                ctx.jobs.c.resolved_at.is_(None),
            )
        ).scalar_one()


def resolve_job(ctx, job_id: str, body: ResolveBody):
    """Record the operator's verdict on an unknown job. Never retries upstream."""
    with ctx.engine.begin() as conn:
        row = (
            conn.execute(select(ctx.jobs).where(ctx.jobs.c.id == job_id))
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Job not found.")
        if row["status"] != "unknown":
            raise HTTPException(
                409,
                f"Only unknown jobs need reconciliation (this job is {row['status']}).",
            )
        if row["resolved_at"]:
            raise HTTPException(409, "This job is already resolved.")
        conn.execute(
            update(ctx.jobs)
            .where(ctx.jobs.c.id == job_id)
            .values(
                resolution=body.resolution,
                resolved_at=ctx.now(),
                resolution_note=(body.note or None),
            )
        )
    ctx.record_audit(
        "job.resolve", {"job_id": job_id, "resolution": body.resolution}
    )
    return ctx.job_detail(job_id)


def list_jobs(ctx, limit: int = 100):
    with ctx.engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(ctx.jobs)
                .order_by(ctx.jobs.c.created_at.desc())
                .limit(min(max(limit, 1), 200))
            )
            .mappings()
            .all()
        ]
        ids = [r["id"] for r in rows]
        grouped = defaultdict(list)
        if ids:
            for image in conn.execute(
                select(ctx.job_images)
                .where(ctx.job_images.c.job_id.in_(ids))
                .order_by(ctx.job_images.c.job_id, ctx.job_images.c.position)
            ).mappings():
                grouped[image["job_id"]].append(image)
    return [ctx.serialize_job(r, image_rows=grouped[r["id"]]) for r in rows]


def job_detail(ctx, job_id: str):
    with ctx.engine.connect() as conn:
        row = (
            conn.execute(select(ctx.jobs).where(ctx.jobs.c.id == job_id))
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(404, "Job not found.")
    return ctx.serialize_job(dict(row))


def agent_job(ctx, job_id: str):
    with ctx.engine.connect() as conn:
        row = (
            conn.execute(select(ctx.jobs).where(ctx.jobs.c.id == job_id))
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(404, "Job not found.")
    return ctx.serialize_job(dict(row), agent=True)


def delete_job(ctx, job_id: str):
    with ctx.engine.connect() as conn:
        row = (
            conn.execute(select(ctx.jobs).where(ctx.jobs.c.id == job_id))
            .mappings()
            .first()
        )
        files = [
            r[0]
            for r in conn.execute(
                select(ctx.job_images.c.image_file).where(
                    ctx.job_images.c.job_id == job_id
                )
            )
        ]
    if not row:
        raise HTTPException(404, "Job not found.")
    if row["status"] == "running":
        raise HTTPException(
            409, "Cannot delete a running job; upstream outcome may already be pending."
        )
    if row["status"] == "queued":
        with ctx.queue_guard:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id, ctx.jobs.c.status == "queued")
                    .values(status="canceled", finished_at=ctx.now())
                )
            ctx.queue_wakeup.set()
        ctx.release_upload(row["reference_upload_id"])
        return {
            "ok": True,
            "message": "Queued job canceled before provider dispatch; job retained for auditing.",
        }
    if row["image_file"]:
        files.append(row["image_file"])
    with ctx.engine.begin() as conn:
        conn.execute(ctx.job_images.delete().where(ctx.job_images.c.job_id == job_id))
        conn.execute(ctx.jobs.delete().where(ctx.jobs.c.id == job_id))
    for filename in set(files):
        if re.fullmatch("[a-f0-9-]{36}(?:-[0-9]{1,2})?\\.(png|jpg|webp)", filename):
            (ctx.DATA / "gallery" / filename).unlink(missing_ok=True)
    ctx.release_upload(row["reference_upload_id"])
    return {"ok": True}
