"""Reference-image uploads for image generation (up to 10 images per job).

Limits are the conservative intersection of:

* OpenAI Images API input rules: up to 10 input images, PNG/JPEG/WEBP, each under 50 MB
  (https://developers.openai.com/api/docs/guides/image-generation);
* the ChatGPT web composer, which caps a single upload at ~20 MB;
* the pinned gateway (v2.16.11), which accepts at most 10 `reference_images` files and
  buffers each upload in memory (MAX_UPLOAD_BYTES=50 MB).

Defaults: 10 files · 20 MiB per file · 50 MiB total · 16 MP per image. They are live
settings and the server enforces them regardless of what the browser sends.

Storage is transient and private: ``data/uploads/<uuid>/`` (mode 0700) contains the
original bytes (never re-encoded), a ``meta.json`` with names/sizes/sha256/dimensions and
a per-file safe multipart name. Files are streamed to disk while hashing, so a 50 MiB
upload never sits in RAM. A job references one upload; the directory is removed when the
job reaches a terminal state, except ``unknown`` (kept for operator reconciliation and
swept by retention).
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
FILE_NAME_RE = re.compile(r"^\d+-[a-f0-9]{12}\.(png|jpg|webp)$")
INCOMING_RE = re.compile(r"^\.incoming-")
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
CHUNK = 1024 * 1024


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


def _probe_pillow(opener, declared_name):
    """Decode the header of an already-validated image; returns (format, w, h, frames)."""
    try:
        with Image.open(opener) as probe:
            width, height = probe.size
            fmt = (probe.format or "").upper()
            frames = int(getattr(probe, "n_frames", 1) or 1)
    except Exception:  # noqa: BLE001 - any decode failure is a bad upload
        raise UploadError(422, f"{declared_name}: the image could not be decoded.")
    return fmt, width, height, frames


def _validate_probe(mime, fmt, width, height, frames, declared_name):
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


def probe_image(data, declared_name="reference"):
    """Validate one reference image held in memory and return its metadata.

    Kept for pure unit testing and small callers; the route streams to disk instead.
    """
    mime = _sniff_mime(data)
    if mime is None:
        raise UploadError(
            415,
            f"{declared_name}: unsupported file type. Use PNG, JPEG or WEBP images "
            "(the provider's image API accepts those three formats).",
        )
    fmt, width, height, frames = _probe_pillow(io.BytesIO(data), declared_name)
    _validate_probe(mime, fmt, width, height, frames, declared_name)
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


async def _stream_to_temp(item, destination, per_file_cap, running_total, total_cap):
    """Write one UploadFile to disk chunk-by-chunk while hashing; enforce both caps."""
    digest = hashlib.sha256()
    size = 0
    with open(destination, "wb") as handle:
        while True:
            chunk = await item.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > per_file_cap:
                raise UploadError(
                    413, f"{item.filename or 'file'}: exceeds the per-file size limit."
                )
            running_total += len(chunk)
            if running_total > total_cap:
                raise UploadError(413, "Attachments exceed the total upload limit.")
            digest.update(chunk)
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    if size == 0:
        raise UploadError(422, "An attached file is empty.")
    return size, running_total, digest.hexdigest()


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
    upload_id = str(uuid.uuid4())
    root = upload_root(ctx.DATA)
    root.mkdir(parents=True, exist_ok=True)
    temp_dir = root / (".incoming-" + upload_id)
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(temp_dir, 0o700)
    except OSError:
        pass
    total = 0
    stored = []
    try:
        for index, item in enumerate(files, start=1):
            display = clean_name(item.filename, f"reference-{index}")
            raw_path = temp_dir / f"raw-{index}.tmp"
            try:
                size, total, sha = await _stream_to_temp(
                    item, raw_path, per_file_cap, total, total_cap
                )
            finally:
                await item.close()
            with open(raw_path, "rb") as handle:
                header = handle.read(16)
            mime = _sniff_mime(header)
            if mime is None:
                raise UploadError(
                    415,
                    f"{display}: unsupported file type. Use PNG, JPEG or WEBP images "
                    "(the provider's image API accepts those three formats).",
                )
            fmt, width, height, frames = _probe_pillow(raw_path, display)
            _validate_probe(mime, fmt, width, height, frames, display)
            ext = SUPPORTED[mime][0]
            final_name = f"{index}-{sha[:12]}.{ext}"
            os.replace(raw_path, temp_dir / final_name)
            try:
                os.chmod(temp_dir / final_name, 0o600)
            except OSError:
                pass
            stored.append(
                {
                    "name": display,
                    "safe_name": f"{index}-{display}",
                    "file": final_name,
                    "mime": mime,
                    "ext": ext,
                    "width": width,
                    "height": height,
                    "bytes": size,
                    "sha256": sha,
                }
            )
        record = {
            "id": upload_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": stored,
            "count": len(stored),
            "total_bytes": total,
            "limits": caps,
        }
        meta_path = temp_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(meta_path, 0o600)
        except OSError:
            pass
        final_dir = root / upload_id
        os.replace(temp_dir, final_dir)
    except UploadError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(exc.status, exc.message) from exc
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - never leak paths
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(500, "Could not store the uploaded images.") from exc

    ctx.record_audit(
        "upload.created",
        {"upload_id": upload_id, "count": len(stored), "bytes": total},
    )
    return {
        "upload_id": upload_id,
        "count": len(stored),
        "total_bytes": total,
        "files": [
            {key: value for key, value in item.items() if key != "file"}
            for item in stored
        ],
        "limits": caps,
    }


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
        file_name = item.get("file")
        # The manifest is ours, but validate it anyway: a tampered meta.json must not
        # turn into a path traversal when the worker opens the references.
        if not isinstance(file_name, str) or not FILE_NAME_RE.match(file_name):
            return None
        path = directory / file_name
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
    """Delete an upload after its job settles or is removed (called by server code)."""
    if not upload_id or not UPLOAD_ID_RE.match(str(upload_id)):
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
            if not entry.is_dir() or INCOMING_RE.match(entry.name):
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
    """Maintenance: drop orphaned/expired uploads, never one used by a live job.

    Half-written ``.incoming-*`` workspaces are dropped after an hour. Completed or
    terminal-job uploads normally delete themselves; this sweep is the safety net for
    crashes and for ``unknown`` jobs whose retention has expired.
    """
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
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - hours * 3600
    incoming_cutoff = now - 3600
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if INCOMING_RE.match(entry.name):
            limit = incoming_cutoff
        else:
            if hours <= 0 or entry.name in active:
                continue
            limit = cutoff
        try:
            if entry.stat().st_mtime >= limit:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    if removed:
        ctx.record_audit("uploads.cleanup", {"removed": removed})
    return removed
