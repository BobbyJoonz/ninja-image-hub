"""Reference-image uploads for image generation (up to 10 images per job).

Limits are the conservative intersection of:

* OpenAI Images API input rules: up to 10 input images, PNG/JPEG/WEBP, each under 50 MB
  (https://developers.openai.com/api/docs/guides/image-generation);
* the ChatGPT web composer, which caps a single upload at ~20 MB;
* the pinned gateway (v2.16.11), which accepts at most 10 `reference_images` files and
  buffers each upload in memory (MAX_UPLOAD_BYTES=50 MB).

Defaults: 10 files · 20 MiB per file · 50 MiB total · 16 MP per image. They are live
settings and the server enforces them regardless of what the browser sends.

Storage is transient and private: ``data/uploads/<uuid>/`` contains the original bytes
(never re-encoded), a ``meta.json`` with names/sizes/sha256/dimensions, mode 0600. A
job references one upload; the directory is removed when the job reaches a terminal
state, except ``unknown`` (kept for operator reconciliation, swept by retention).
"""

import hashlib
import io
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import File, HTTPException, UploadFile
from PIL import Image

MAX_FILES = 10
MAX_FILE_MB = 20
MAX_TOTAL_MB = 50
MAX_PIXELS = 16_777_216
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SUPPORTED = {
    "image/png": ("png", "PNG"),
    "image/jpeg": ("jpg", "JPEG"),
    "image/webp": ("webp", "WEBP"),
}
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def upload_root(data_dir):
    return Path(data_dir) / "uploads"


def limits(ctx):
    return {
        "max_files": min(MAX_FILES, int(ctx.setting("upload_max_files"))),
        "max_file_mb": min(50, int(ctx.setting("upload_max_file_mb"))),
        "max_total_mb": min(500, int(ctx.setting("upload_max_total_mb"))),
        "max_pixels": MAX_PIXELS,
        "allowed_types": sorted(SUPPORTED),
    }


def _sniff_mime(data):
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in MAGIC:
        if data.startswith(magic):
            return mime
    return None


def clean_name(name, fallback):
    base = os.path.basename(str(name or "")).strip() or fallback
    base = SAFE_NAME_RE.sub("_", base)
    base = base.lstrip(".")[:120]
    return base or fallback


def probe_image(data, declared_name="reference"):
    """Validate one reference image and return its metadata (never re-encodes)."""
    mime = _sniff_mime(data)
    if mime is None:
        raise UploadError(
            415,
            f"{declared_name}: unsupported file type. Use PNG, JPEG or WEBP images "
            "(the provider's image API accepts those three formats).",
        )
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            fmt = (probe.format or "").upper()
            frames = int(getattr(probe, "n_frames", 1) or 1)
    except Exception:  # noqa: BLE001 - any decode failure is a bad upload
        raise UploadError(422, f"{declared_name}: the image could not be decoded.")
    if SUPPORTED[mime][1] != fmt:
        raise UploadError(422, f"{declared_name}: file content does not match its type.")
    if frames > 1:
        raise UploadError(422, f"{declared_name}: animated images are not supported.")
    if width * height > MAX_PIXELS:
        raise UploadError(
            422,
            f"{declared_name}: {width}x{height} exceeds the {MAX_PIXELS:,}-pixel input limit.",
        )
    if width < 1 or height < 1:
        raise UploadError(422, f"{declared_name}: empty image.")
    ext = SUPPORTED[mime][0]
    return {
        "name": clean_name(declared_name, f"reference.{ext}"),
        "mime": mime,
        "ext": ext,
        "width": width,
        "height": height,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": data,
    }


async def create_upload(ctx, files: list[UploadFile] = File(...)):
    """POST /api/uploads (admin+CSRF) and /v1/uploads (Bearer agent)."""
    if not files:
        raise HTTPException(422, "Attach at least one image.")
    caps = limits(ctx)
    if len(files) > caps["max_files"]:
        raise HTTPException(
            422,
            f"Too many images: {len(files)}. The limit is {caps['max_files']} per request.",
        )
    if getattr(ctx, "disk_low", False):
        raise HTTPException(507, "Low disk: uploads are paused until space is freed.")

    per_file_cap = caps["max_file_mb"] * 1024 * 1024
    total_cap = caps["max_total_mb"] * 1024 * 1024
    prepared = []
    total = 0
    try:
        for index, item in enumerate(files):
            label = clean_name(item.filename, f"reference-{index + 1}")
            data = await item.read(per_file_cap + 1)
            if len(data) > per_file_cap:
                raise UploadError(
                    413,
                    f"{label}: larger than {caps['max_file_mb']} MiB per-file limit.",
                )
            total += len(data)
            if total > total_cap:
                raise UploadError(
                    413,
                    f"Attachments exceed the {caps['max_total_mb']} MiB total limit.",
                )
            try:
                prepared.append(probe_image(data, label))
            except UploadError:
                raise
            finally:
                await item.close()
        if not prepared:
            raise UploadError(422, "No usable images were attached.")

        upload_id = str(uuid.uuid4())
        root = upload_root(ctx.DATA)
        final_dir = root / upload_id
        temp_dir = root / (".incoming-" + upload_id)
        temp_dir.mkdir(parents=True, exist_ok=False)
        stored = []
        try:
            for index, meta in enumerate(prepared, start=1):
                filename = f"{index}-{meta['sha256'][:12]}.{meta['ext']}"
                path = temp_dir / filename
                with open(path, "wb") as handle:
                    handle.write(meta.pop("data"))
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                stored.append({**meta, "file": filename})
            record = {
                "id": upload_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": stored,
                "count": len(stored),
                "total_bytes": total,
                "limits": caps,
            }
            temp_meta = temp_dir / "meta.json"
            with open(temp_meta, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_meta, 0o600)
            except OSError:
                pass
            os.replace(temp_dir, final_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        ctx.record_audit(
            "upload.created",
            {"upload_id": upload_id, "count": len(stored), "bytes": total},
        )
        return {
            "upload_id": upload_id,
            "count": len(stored),
            "total_bytes": total,
            "files": [{k: v for k, v in item.items() if k != "file"} for item in stored],
            "limits": caps,
        }
    except UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never leak paths
        raise HTTPException(500, "Could not store the uploaded images.") from exc


def load_upload(ctx, upload_id):
    """Return the stored upload (with absolute file paths) or None."""
    if not upload_id or not UPLOAD_ID_RE.match(str(upload_id)):
        return None
    directory = upload_root(ctx.DATA) / upload_id
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        record = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = []
    for item in record.get("files", []):
        path = directory / item["file"]
        if not path.is_file():
            return None
        files.append({**item, "path": path})
    if not files:
        return None
    record["files"] = files
    record["dir"] = directory
    return record


def delete_upload(ctx, upload_id, force=False):
    """DELETE /api/uploads/{id}: remove an unused upload (refused while a job uses it)."""
    if not upload_id or not UPLOAD_ID_RE.match(str(upload_id)):
        raise HTTPException(404, "Upload not found.")
    directory = upload_root(ctx.DATA) / upload_id
    if not directory.is_dir():
        raise HTTPException(404, "Upload not found.")
    if not force:
        from sqlalchemy import select

        with ctx.engine.connect() as conn:
            busy = conn.execute(
                select(ctx.jobs.c.id)
                .where(
                    ctx.jobs.c.reference_upload_id == upload_id,
                    ctx.jobs.c.status.in_(("queued", "running")),
                )
                .limit(1)
            ).first()
        if busy:
            raise HTTPException(409, "Upload is in use by a queued or running job.")
    shutil.rmtree(directory, ignore_errors=True)
    return {"ok": True, "upload_id": upload_id}


def release_upload(ctx, upload_id):
    """Delete an upload after its job settles (called by the scheduler)."""
    if not upload_id:
        return
    directory = upload_root(ctx.DATA) / upload_id
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)


def usage(ctx):
    """Cheap inventory for the Operations panel (count + bytes on disk)."""
    root = upload_root(ctx.DATA)
    count = 0
    size = 0
    if root.is_dir():
        for entry in root.iterdir():
            if not entry.is_dir() or entry.name.startswith(".incoming-"):
                continue
            count += 1
            try:
                for item in entry.iterdir():
                    if item.is_file():
                        size += item.stat().st_size
            except OSError:
                continue
    return {"count": count, "bytes": size}


def sweep_uploads(ctx, hours):
    """Maintenance: drop orphaned/expired uploads, never one used by a live job."""
    if hours <= 0:
        return 0
    root = upload_root(ctx.DATA)
    if not root.is_dir():
        return 0
    from sqlalchemy import select

    with ctx.engine.connect() as conn:
        active = {
            row[0]
            for row in conn.execute(
                select(ctx.jobs.c.reference_upload_id).where(
                    ctx.jobs.c.reference_upload_id.is_not(None),
                    ctx.jobs.c.status.in_(("queued", "running")),
                )
            )
        }
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in active:
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    if removed:
        ctx.record_audit("uploads.cleanup", {"removed": removed})
    return removed
