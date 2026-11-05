"""Authenticated previews, cursor pagination and bounded ZIP delivery."""

import base64
import json
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import and_, or_, select
from starlette.background import BackgroundTask

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


def preview_image(ctx, job_id: str, position: int):
    original = ctx.read_image_file(job_id, position)
    cache = ctx.DATA / "previews"
    cache.mkdir(exist_ok=True)
    # Stat includes nanoseconds so a restored file never reuses a stale preview.
    stat = original.stat()
    name = f"{original.stem}-{stat.st_mtime_ns}-{stat.st_size}.webp"
    destination = cache / name
    with ctx.preview_slots:
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix=".preview-", dir=cache)
            os.close(fd)
            try:
                with Image.open(original) as image:
                    # Bound preview decoding. The original remains available.
                    if image.width * image.height > 24_000_000:
                        raise HTTPException(
                            422,
                            "Preview unavailable for images above 24 megapixels. Download original.",
                        )
                    image.draft("RGB", (640, 640))
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((640, 640))
                    image.convert("RGB").save(
                        temporary, format="WEBP", quality=78, method=2
                    )
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return FileResponse(destination, media_type="image/webp", headers=PRIVATE_HEADERS)


def gallery_page(ctx, limit: int = 24, cursor: str | None = None):
    if not 1 <= limit <= 100:
        raise HTTPException(422, "Gallery limit must be between 1 and 100.")
    query = select(
        ctx.job_images,
        ctx.jobs.c.prompt,
        ctx.jobs.c.aspect_ratio,
        ctx.jobs.c.quality_preference,
        ctx.jobs.c.account_id,
    ).join(ctx.jobs, ctx.jobs.c.id == ctx.job_images.c.job_id)
    if cursor:
        try:
            if len(cursor) > 300:
                raise ValueError()
            jid, position = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(jid, str) or not isinstance(position, int):
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Invalid gallery cursor.")
        query = query.where(
            or_(
                ctx.job_images.c.job_id < jid,
                and_(
                    ctx.job_images.c.job_id == jid, ctx.job_images.c.position > position
                ),
            )
        )
    # UUID + output position is immutable; stable keyset traversal avoids OFFSET
    # scans and duplicate rows when new outputs arrive. Not chronological order.
    query = query.order_by(
        ctx.job_images.c.job_id.desc(), ctx.job_images.c.position
    ).limit(limit + 1)
    with ctx.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query).mappings()]
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        jid, pos = row["job_id"], row["position"]
        items.append(
            {
                k: row[k]
                for k in (
                    "job_id",
                    "position",
                    "prompt",
                    "aspect_ratio",
                    "quality_preference",
                    "account_id",
                    "image_width",
                    "image_height",
                    "image_format",
                    "image_bytes",
                    "image_sha256",
                    "stored_at",
                )
            }
            | {
                "url": f"/api/images/{jid}/{pos}",
                "preview_url": f"/api/previews/{jid}/{pos}",
            }
        )
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps([rows[-1]["job_id"], rows[-1]["position"]]).encode()
        ).decode()
        if more
        else None
    )
    return {"items": items, "next_cursor": next_cursor}


def export_job(ctx, job_id: str):
    if not ctx.export_slots.acquire(blocking=False):
        raise HTTPException(
            429, "Two exports are already active. Try again after a download finishes."
        )
    temporary = None
    try:
        with ctx.engine.connect() as conn:
            job = conn.execute(
                select(ctx.jobs.c.status).where(ctx.jobs.c.id == job_id)
            ).first()
            positions = list(
                conn.execute(
                    select(ctx.job_images.c.position)
                    .where(ctx.job_images.c.job_id == job_id)
                    .order_by(ctx.job_images.c.position)
                ).scalars()
            )
        if not job:
            raise HTTPException(404, "Job not found.")
        if job[0] == "running":
            raise HTTPException(409, "Wait until the job stops before exporting.")
        if not positions:
            # Legacy one-output route remains exportable after an older DB upgrade.
            ctx.read_image_file(job_id, 0)
            positions = [0]
        paths = [ctx.read_image_file(job_id, p) for p in positions]
        if len(paths) > 10 or sum(p.stat().st_size for p in paths) > 640 * 1024 * 1024:
            raise HTTPException(413, "Export exceeds the per-job budget.")
        folder = ctx.DATA / "exports"
        folder.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="job-", suffix=".zip", dir=folder)
        os.close(fd)
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for path in paths:
                archive.write(path, arcname=path.name)

        def cleanup():
            try:
                Path(temporary).unlink(missing_ok=True)
            finally:
                ctx.export_slots.release()

        return FileResponse(
            temporary,
            filename=f"ninja-{job_id}.zip",
            media_type="application/zip",
            headers=PRIVATE_HEADERS,
            background=BackgroundTask(cleanup),
        )
    except BaseException:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        ctx.export_slots.release()
        raise
