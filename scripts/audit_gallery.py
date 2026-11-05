"""Read-only file/SQL reconciliation report. Never deletes or alters data."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    data = Path(os.getenv("DATA_DIR", str(root / "data"))).resolve()
    if not data.is_dir():
        raise SystemExit("Data directory does not exist; nothing was changed.")
    url = os.getenv("DATABASE_URL", "sqlite:///" + str(data / "hub.db"))
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and (
        not parsed.database or not Path(parsed.database).is_file()
    ):
        raise SystemExit(
            "SQLite database does not exist; refusing to create one during audit."
        )
    engine = create_engine(url)
    references = set()
    try:
        with engine.connect() as conn:
            schema = inspect(conn)
            for table in ("jobs", "job_images"):
                if schema.has_table(table):
                    references.update(
                        row[0]
                        for row in conn.exec_driver_sql(
                            f"SELECT image_file FROM {table} WHERE image_file IS NOT NULL"
                        )
                    )
    finally:
        engine.dispose()
    gallery = data / "gallery"
    files = (
        {p.name for p in gallery.iterdir() if p.is_file() or p.is_symlink()}
        if gallery.is_dir()
        else set()
    )
    incoming = sorted(n for n in files if n.startswith(".incoming-"))
    report = {
        "read_only": True,
        "referenced_files": len(references),
        "missing": sorted(references - files),
        "unindexed": sorted(files - references - set(incoming)),
        "incoming_temporary": incoming,
        "export_temporary": sorted(p.name for p in (data / "exports").glob("*.zip")),
        "preview_cache_files": sum(
            1 for p in (data / "previews").glob("*") if p.is_file()
        ),
        "note": "Stop workers and review backups before deciding whether any listed file should be recovered or removed. No deletion performed.",
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
