import re
import uuid
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, update


from .media import store_original


def write_image_payload(ctx, data: dict, job_id: str, account_port: int):
    """Legacy single-output entry point, preserving original bytes."""
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("Gateway did not return image data.")
    return store_original(ctx.DATA / "gallery", job_id, items[0], account_port)


def record_image_batch(
    ctx, data: dict, job_id: str, account_port: int, expected_n: int
):
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("Gateway returned no images.")
    if len(items) > expected_n:
        raise ValueError("Gateway returned more images than requested.")
    stored = []
    for position, item in enumerate(items):
        filename = None
        try:
            filename, metadata = store_original(
                ctx.DATA / "gallery", f"{job_id}-{position + 1}", item, account_port
            )
            try:
                with ctx.engine.begin() as conn:
                    conn.execute(
                        ctx.job_images.insert().values(
                            id=str(uuid.uuid4()),
                            job_id=job_id,
                            position=position,
                            image_file=filename,
                            stored_at=ctx.now(),
                            **metadata,
                        )
                    )
                    if position == 0:
                        conn.execute(
                            update(ctx.jobs)
                            .where(ctx.jobs.c.id == job_id)
                            .values(image_file=filename, **metadata)
                        )
            except Exception:
                # The row transaction rolled back: do not leave an invisible
                # original that blocks recovery. Earlier outputs stay durable.
                (ctx.DATA / "gallery" / filename).unlink(missing_ok=True)
                raise
            stored.append((filename, metadata))
        except Exception as exc:
            return stored, f"Image #{position + 1} could not be saved: {str(exc)[:160]}"
    return stored, None


def read_image_file(ctx, job_id, position):
    with ctx.engine.connect() as conn:
        row = conn.execute(
            select(ctx.job_images.c.image_file).where(
                ctx.job_images.c.job_id == job_id, ctx.job_images.c.position == position
            )
        ).first()
        if not row and position == 0:
            row = conn.execute(
                select(ctx.jobs.c.image_file).where(ctx.jobs.c.id == job_id)
            ).first()
    if not row or not row[0]:
        raise HTTPException(404, "Image not found.")
    filename = row[0]
    if not re.fullmatch("[0-9a-f-]{36}(?:-[0-9]{1,2})?\\.(png|jpg|webp)", filename):
        raise HTTPException(404, "Invalid image path.")
    path = ctx.DATA / "gallery" / filename
    if path.is_symlink() or path.resolve().parent != (ctx.DATA / "gallery").resolve():
        raise HTTPException(404, "Invalid image path.")
    if not path.is_file():
        raise HTTPException(404, "Image file missing.")
    return path


def image_index_download(ctx, job_id: str, position: int, download: bool = False):
    path = ctx.read_image_file(job_id, position)
    return FileResponse(
        path,
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def agent_index_download(ctx, job_id: str, position: int):
    path = ctx.read_image_file(job_id, position)
    return FileResponse(
        path,
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def image_download(ctx, job_id: str, download: bool = False):
    return ctx.image_index_download(job_id, 0, download)


def agent_image_download(ctx, job_id: str):
    return ctx.agent_index_download(job_id, 0)
