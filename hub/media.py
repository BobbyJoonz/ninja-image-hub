"""Bounded image transport and atomic original-file storage.

No image is re-encoded on the original download path. Network chunks and base64
chunks are capped; final filenames only become visible after validation + fsync.
"""

import base64
import hashlib
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
MAX_PIXELS = 90_000_000


def approved_gateway_url(url, account_port):
    p = urlparse(url)
    if (
        p.scheme != "http"
        or p.hostname not in ("127.0.0.1", "localhost")
        or p.port != account_port
        or p.query
        or p.fragment
        or p.username
        or p.password
        or not re.fullmatch(r"/images/[a-zA-Z0-9_-]+\.(png|webp|jpg|jpeg)", p.path)
    ):
        raise ValueError(
            "Gateway returned an unexpected image location (SSRF rejected)."
        )
    return f"http://127.0.0.1:{account_port}{p.path}"


def image_chunks(item, account_port):
    if not isinstance(item, dict):
        raise ValueError("Invalid image object from gateway.")
    encoded = item.get("b64_json")
    if isinstance(encoded, str):
        if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("Image too large (64 MiB safety cap).")
        # Multiple-of-four boundaries retain strict base64 validation without a
        # second full-size byte copy. Reject padding before the final chunk.
        for start in range(0, len(encoded), CHUNK_BYTES):
            part = encoded[start : start + CHUNK_BYTES]
            if "=" in part and start + CHUNK_BYTES < len(encoded):
                raise ValueError("Invalid base64 padding.")
            yield base64.b64decode(part, validate=True)
    elif isinstance(item.get("url"), str):
        url = approved_gateway_url(item["url"], account_port)
        with httpx.stream(
            "GET", url, trust_env=False, timeout=60, follow_redirects=False
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Gateway image redirect or partial response rejected.")
            length = response.headers.get("content-length")
            if length and int(length) > MAX_IMAGE_BYTES:
                raise ValueError("Image too large (64 MiB safety cap).")
            yield from response.iter_bytes(CHUNK_BYTES)
    else:
        raise ValueError("Image was neither base64 nor an approved gateway URL.")


def validate_image(path):
    try:
        with Image.open(path) as image:
            width, height = image.size
            fmt = image.format
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise ValueError("Unsafe image dimensions.")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Gateway returned a non-image file or unsafe image.") from exc
    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(fmt)
    if not extension:
        raise ValueError("Unsupported image type.")
    return extension, {
        "image_width": width,
        "image_height": height,
        "image_format": fmt,
    }


def store_original(directory, stem, item, account_port):
    if not re.fullmatch(r"[0-9a-f-]{36}(?:-[0-9]{1,2})?", stem):
        raise ValueError("Invalid output identifier.")
    directory = Path(directory)
    fd, temporary = tempfile.mkstemp(prefix=".incoming-", dir=directory)
    temporary = Path(temporary)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as target:
            for chunk in image_chunks(item, account_port):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("Image too large (64 MiB safety cap).")
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        extension, metadata = validate_image(temporary)
        filename = stem + "." + extension
        # Atomic no-clobber publish: a duplicate result cannot replace an original.
        os.link(temporary, directory / filename)
        return filename, {
            **metadata,
            "image_bytes": total,
            "image_sha256": digest.hexdigest(),
        }
    finally:
        temporary.unlink(missing_ok=True)
