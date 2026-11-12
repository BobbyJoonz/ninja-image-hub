"""Single source of truth for the release version.

``package.json`` is the canonical value (the frontend/package metadata already lives there);
the Python runtime and ``/api/health`` read it instead of duplicating the literal in three
places.
"""

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _read_version() -> str:
    try:
        data = json.loads((_ROOT / "package.json").read_text(encoding="utf-8"))
        value = str(data.get("version") or "").strip()
        return value or "0.0.0"
    except (OSError, ValueError, TypeError):
        return "0.0.0"


VERSION = _read_version()
