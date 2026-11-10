"""Regenerate and verify the release seal (MANIFEST_SHA256.json).

The seal must be platform-stable: a Linux clone and a Windows checkout must verify the
same hashes. Git may normalise line endings on checkout, so text files are hashed after
CRLF -> LF normalisation; binary files (images, zip, db) are hashed byte-for-byte.

    python scripts/seal_manifest.py            # regenerate
    python scripts/seal_manifest.py --check    # verify only, exit 1 on drift
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST_SHA256.json"
SKIP_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "data",
    "vendor",
    ".git",
    "backups",
    "node_modules",
}
SKIP_NAMES = {".env", "MANIFEST_SHA256.json"}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".css",
    ".js",
    ".mjs",
    ".svg",
    ".html",
    ".htm",
    ".sh",
    ".bat",
    ".ps1",
    ".example",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".service",
    ".timer",
}
TEXT_NAMES = {".gitignore", ".gitattributes", "Dockerfile", "Makefile"}


def is_text(path):
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def digest(path):
    data = path.read_bytes()
    if is_text(path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def entries():
    result = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name in SKIP_NAMES or rel.as_posix() in SKIP_NAMES:
            continue
        result[rel.as_posix()] = digest(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the current seal")
    args = parser.parse_args()
    if args.check:
        if not MANIFEST.is_file():
            sys.exit("MANIFEST_SHA256.json is missing")
        sealed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        current = entries()
        missing = sorted(set(sealed) - set(current))
        extra = sorted(set(current) - set(sealed))
        drift = sorted(name for name in sealed if name in current and sealed[name] != current[name])
        if missing or extra or drift:
            print(f"SEAL MISMATCH: {len(missing)} missing, {len(extra)} extra, {len(drift)} drifted")
            for name in (missing + extra + drift)[:20]:
                print(" ", name)
            return 1
        print(f"seal OK: {len(sealed)} files")
        return 0
    data = entries()
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(data)} entries -> {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
