"""Back up and restore the Hub's durable state (SQLite + originals + account sessions).

    python scripts/backup.py create --dest D:\\backups                # snapshot now
    python scripts/backup.py verify --path D:\\backups\\2026-11-06_1200
    python scripts/backup.py restore --path ... --target D:\\new-data --force

What is included: an online SQLite snapshot of ``hub.db`` (WAL-safe), ``.env`` (mode
0600), ``gallery/`` originals, and per-account ``auto-login.json`` + ``gateway/auth/``
session state. What is **not** included by default: WebP previews, temporary export
ZIPs and worker logs — they are derived/regenerable and can be huge.

``verify`` re-hashes every file against ``backup.json``; ``restore`` refuses a non-empty
target unless ``--force`` is given and never touches the source.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - dotenv is a runtime dependency
    pass


def data_dir():
    return Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()


def db_path():
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :]).resolve()
    return data_dir() / "hub.db"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite(source, destination):
    """Consistent copy of a live SQLite database (WAL included) via the backup API."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def collect_files(data, include_derived=False):
    """Relative paths of everything worth backing up (sorted, deterministic)."""
    files = []
    gallery = data / "gallery"
    if gallery.is_dir():
        files.extend(sorted(gallery.glob("*")))
    if include_derived:
        previews = data / "previews"
        if previews.is_dir():
            files.extend(sorted(previews.glob("*.webp")))
    accounts = data / "accounts"
    if accounts.is_dir():
        for account in sorted(accounts.iterdir()):
            if not account.is_dir():
                continue
            for name in ("auto-login.json",):
                candidate = account / name
                if candidate.is_file():
                    files.append(candidate)
            auth = account / "gateway" / "auth"
            if auth.is_dir():
                files.extend(sorted(p for p in auth.rglob("*") if p.is_file()))
    return files


def create(args):
    data = data_dir()
    if not data.is_dir():
        sys.exit(f"Data directory not found: {data}")
    database = db_path()
    destination = Path(args.dest).resolve()
    if destination.exists() and any(destination.iterdir()):
        if not args.force:
            sys.exit(f"{destination} is not empty; pass --force to add another backup there")
    destination.mkdir(parents=True, exist_ok=True)

    entries = []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if database.is_file():
        target = destination / "hub.db"
        snapshot_sqlite(database, target)
        entries.append(
            {"path": "hub.db", "bytes": target.stat().st_size, "sha256": sha256_of(target)}
        )
        print(f"database snapshot: {target} ({target.stat().st_size} bytes)")
    else:
        print(f"warning: no SQLite database at {database}; skipping it")

    env_file = ROOT / ".env"
    if env_file.is_file():
        target = destination / ".env"
        shutil.copy2(env_file, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        entries.append(
            {"path": ".env", "bytes": target.stat().st_size, "sha256": sha256_of(target)}
        )
        print("secrets file copied (mode 0600)")

    for source in collect_files(data, include_derived=args.include_derived):
        relative = source.relative_to(data)
        target = destination / "data" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entries.append(
            {
                "path": str(Path("data") / relative).replace("\\", "/"),
                "bytes": target.stat().st_size,
                "sha256": sha256_of(target),
            }
        )
    total = sum(item["bytes"] for item in entries)
    metadata = {
        "format": 1,
        "created_at": started,
        "source_data_dir": str(data),
        "files": entries,
        "file_count": len(entries),
        "total_bytes": total,
        "includes_derived": bool(args.include_derived),
        "note": "Contains ChatGPT session state and .env secrets. Store encrypted and private.",
    }
    (destination / "backup.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"backup written: {destination}")
    print(f"files: {len(entries)} | bytes: {total}")
    return 0


def verify(args):
    path = Path(args.path).resolve()
    metadata_file = path / "backup.json"
    if not metadata_file.is_file():
        sys.exit(f"{metadata_file} is missing; not a backup directory")
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    bad = []
    for item in metadata.get("files", []):
        target = path / item["path"]
        if not target.is_file():
            bad.append((item["path"], "missing"))
            continue
        if target.stat().st_size != item["bytes"]:
            bad.append((item["path"], "size"))
            continue
        if sha256_of(target) != item["sha256"]:
            bad.append((item["path"], "sha256"))
    if bad:
        print(f"BACKUP CORRUPT: {len(bad)} problem(s)")
        for name, why in bad[:20]:
            print(f"  {why}: {name}")
        return 1
    print(
        f"backup OK: {metadata.get('file_count')} files, "
        f"{metadata.get('total_bytes')} bytes, created {metadata.get('created_at')}"
    )
    return 0


def restore(args):
    path = Path(args.path).resolve()
    target = Path(args.target).resolve()
    metadata_file = path / "backup.json"
    if not metadata_file.is_file():
        sys.exit(f"{metadata_file} is missing; not a backup directory")
    if target == data_dir() and not args.force:
        sys.exit("Refusing to restore over the live data directory without --force")
    if target.exists() and any(target.iterdir()) and not args.force:
        sys.exit(f"{target} is not empty; stop the panel, empty it, or pass --force")
    if args.dry_run:
        print(f"would restore {metadata_file.parent} -> {target}")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path / "hub.db", target / "hub.db")
    if (path / ".env").is_file() and not (ROOT / ".env").exists():
        shutil.copy2(path / ".env", ROOT / ".env")
        try:
            os.chmod(ROOT / ".env", 0o600)
        except OSError:
            pass
        print("restored .env (it was missing)")
    for source in sorted((path / "data").rglob("*")) if (path / "data").is_dir() else []:
        if source.is_file():
            destination = target / source.relative_to(path / "data")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    print(f"restored into {target}. Start the panel with DATA_DIR={target}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create", help="write a backup directory")
    create_parser.add_argument(
        "--dest",
        default=str(ROOT / "backups" / time.strftime("%Y%m%d-%H%M%S")),
        help="destination directory (default: ./backups/<timestamp>)",
    )
    create_parser.add_argument(
        "--include-derived",
        action="store_true",
        help="also copy WebP previews (regenerable, can be large)",
    )
    create_parser.add_argument("--force", action="store_true", help="write into a non-empty directory")
    create_parser.set_defaults(func=create)

    verify_parser = sub.add_parser("verify", help="re-hash a backup")
    verify_parser.add_argument("--path", required=True)
    verify_parser.set_defaults(func=verify)

    restore_parser = sub.add_parser("restore", help="restore into a directory")
    restore_parser.add_argument("--path", required=True)
    restore_parser.add_argument("--target", required=True, help="data directory to restore into")
    restore_parser.add_argument("--dry-run", action="store_true")
    restore_parser.add_argument("--force", action="store_true")
    restore_parser.set_defaults(func=restore)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
