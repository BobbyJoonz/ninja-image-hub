"""Synthetic local file-transfer benchmark; never contacts an image provider."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
import uuid

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.media import store_original


def measure(function):
    tracemalloc.start()
    start = time.perf_counter()
    value = function()
    duration = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, {
        "seconds": round(duration, 4),
        "python_peak_mib": round(peak / 1048576, 3),
    }


def main():
    buffer = io.BytesIO()
    Image.frombytes("RGB", (1600, 1600), os.urandom(1600 * 1600 * 3)).save(
        buffer, "PNG", compress_level=0
    )
    raw = buffer.getvalue()
    encoded = base64.b64encode(raw).decode()
    digest = hashlib.sha256(raw).hexdigest()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)

        def old():
            decoded = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(decoded)) as image:
                image.verify()
            (root / "legacy.png").write_bytes(decoded)

        _, legacy = measure(old)
        (name, metadata), chunked = measure(
            lambda: store_original(
                root, str(uuid.uuid4()), {"b64_json": encoded}, 41000
            )
        )
        assert metadata["image_sha256"] == digest
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    print(
        json.dumps(
            {
                "fixture_bytes": len(raw),
                "dimensions": [1600, 1600],
                "scope": "storage stage only; preallocated base64 input; tracemalloc is Python heap, not RSS, JSON parsing or browser RAM",
                "legacy": legacy,
                "v05_chunked": chunked,
                "original_sha256_matches": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
