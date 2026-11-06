"""SQL schema, additive migrations and account lookup. Never resets user data."""

import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    select,
    update,
    inspect,
)
from sqlalchemy.engine import make_url


def _sqlite_flavour(db_url):
    """Return (is_sqlite, is_memory) for any SQLAlchemy URL form.

    String prefixes miss `sqlite+pysqlite:///...`, which silently disabled the WAL
    hardening; the URL parser sees every dialect spelling.
    """
    try:
        url = make_url(db_url)
    except Exception:
        return (False, False)
    if url.get_backend_name() != "sqlite":
        return (False, False)
    return (True, url.database in (None, "", ":memory:"))


def initialize_database(ctx):
    ctx.DB_URL = os.getenv(
        "DATABASE_URL", "sqlite:///" + str(ctx.DATA / "hub.db").replace("\\", "/")
    )

    is_sqlite, is_memory = _sqlite_flavour(ctx.DB_URL)
    engine_kwargs = {"pool_pre_ping": True}
    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if is_memory:
            # An in-memory database lives inside one connection; without a shared pool
            # every thread would open its own empty database.
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool
    ctx.engine = create_engine(ctx.DB_URL, **engine_kwargs)

    if is_sqlite:

        @event.listens_for(ctx.engine, "connect")
        def _apply_sqlite_pragmas(dbapi_connection, _record):
            # WAL keeps the scheduler thread, the 16 job threads and the HTTP workers from
            # fighting over a single write lock. Without it a busy SQLite database raises
            # "database is locked" inside record_image_batch, which deletes the
            # just-published original and turns a successful generation into a lost image.
            # These are per-connection settings, so they are applied on every new connection.
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                if not is_memory:
                    cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

    ctx.meta = MetaData()

    ctx.accounts = Table(
        "accounts",
        ctx.meta,
        Column("id", String(36), primary_key=True),
        Column("name", String(80), nullable=False),
        Column("email_label", String(160), nullable=False, default=""),
        Column("port", Integer, nullable=False, unique=True),
        Column("api_secret", String(100), nullable=False),
        Column("autostart", Integer, nullable=False, default=1),
        Column("archived_at", String(40)),
        Column("plan_label", String(16), nullable=False, default="unknown"),
        Column("plan_checked_at", String(40)),
        Column("parallel_limit", Integer, nullable=False, default=2),
        Column("dispatch_enabled", Integer, nullable=False, default=1),
        Column("cooldown_until", String(40)),
        Column("project_url", String(500), nullable=False, default=""),
        Column("plan_meta", String(2000), nullable=False, default=""),
        Column("created_at", String(40), nullable=False),
    )

    ctx.jobs = Table(
        "jobs",
        ctx.meta,
        Column("id", String(36), primary_key=True),
        Column("account_id", String(36), ForeignKey("accounts.id"), nullable=False),
        Column("prompt", Text, nullable=False),
        Column("aspect_ratio", String(8), nullable=False),
        Column("quality_preference", String(16), nullable=False, default="best"),
        Column("image_width", Integer),
        Column("image_height", Integer),
        Column("image_format", String(12)),
        Column("image_bytes", Integer),
        Column("image_sha256", String(64)),
        Column("status", String(20), nullable=False),
        Column("error", Text),
        Column("n", Integer, nullable=False, default=1),
        Column("idempotency_key", String(128)),
        Column("image_file", String(120)),
        Column("created_at", String(40), nullable=False),
        Column("started_at", String(40)),
        Column("finished_at", String(40)),
        # Operator reconciliation for upstream-uncertain jobs (never automatic).
        Column("resolution", String(32)),
        Column("resolved_at", String(40)),
        Column("resolution_note", Text),
    )

    ctx.job_images = Table(
        "job_images",
        ctx.meta,
        Column("id", String(36), primary_key=True),
        Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False),
        Column("position", Integer, nullable=False),
        Column("image_file", String(150), nullable=False),
        Column("stored_at", String(40)),
        Column("image_width", Integer),
        Column("image_height", Integer),
        Column("image_format", String(12)),
        Column("image_bytes", Integer),
        Column("image_sha256", String(64)),
    )

    ctx.control_settings = Table(
        "control_settings",
        ctx.meta,
        Column("key", String(60), primary_key=True),
        Column("value", String(100), nullable=False),
        Column("updated_at", String(40), nullable=False),
    )

    ctx.control_audit = Table(
        "control_audit",
        ctx.meta,
        Column("id", String(36), primary_key=True),
        Column("action", String(80), nullable=False),
        Column("detail", Text, nullable=False),
        Column("created_at", String(40), nullable=False),
    )

    ctx.meta.create_all(ctx.engine)

    with ctx.engine.begin() as conn:
        inspector = inspect(conn)
        for table_name, additions in {
            "accounts": {
                "archived_at": "VARCHAR(40)",
                "plan_label": "VARCHAR(16) DEFAULT 'unknown' NOT NULL",
                "plan_checked_at": "VARCHAR(40)",
                "parallel_limit": "INTEGER DEFAULT 2 NOT NULL",
                "cooldown_until": "VARCHAR(40)",
                "dispatch_enabled": "INTEGER DEFAULT 1 NOT NULL",
                "project_url": "VARCHAR(500) DEFAULT '' NOT NULL",
                "plan_meta": "VARCHAR(2000) DEFAULT '' NOT NULL",
            },
            "jobs": {
                "quality_preference": "VARCHAR(16) DEFAULT 'best' NOT NULL",
                "image_width": "INTEGER",
                "image_height": "INTEGER",
                "image_format": "VARCHAR(12)",
                "image_bytes": "INTEGER",
                "image_sha256": "VARCHAR(64)",
                "n": "INTEGER DEFAULT 1 NOT NULL",
                "idempotency_key": "VARCHAR(128)",
                "resolution": "VARCHAR(32)",
                "resolved_at": "VARCHAR(40)",
                "resolution_note": "TEXT",
            },
        }.items():
            current = {c["name"] for c in inspector.get_columns(table_name)}
            for column, ddl in additions.items():
                if column not in current:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}"
                    )

    with ctx.engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_account_idem ON jobs(account_id, idempotency_key)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_images_position ON job_images(job_id, position)"
        )
        conn.execute(
            update(ctx.jobs)
            .where(ctx.jobs.c.status == "running")
            .values(
                status="unknown",
                error="Server restarted during execution. Outcome may exist upstream; never auto-retry.",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if "stored_at" not in {
            col["name"] for col in inspect(conn).get_columns("job_images")
        }:
            conn.exec_driver_sql(
                "ALTER TABLE job_images ADD COLUMN stored_at VARCHAR(40)"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_job_images_stored_at ON job_images(stored_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs(created_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_finished_at ON jobs(finished_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs(status, account_id, created_at, id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_jobs_status_resolution ON jobs(status, resolved_at)"
        )
        legacy = (
            conn.execute(
                select(ctx.jobs).where(
                    ctx.jobs.c.image_file.is_not(None),
                    ~select(ctx.job_images.c.id)
                    .where(ctx.job_images.c.job_id == ctx.jobs.c.id)
                    .exists(),
                )
            )
            .mappings()
            .all()
        )
        for row in legacy:
            exists = conn.execute(
                select(ctx.job_images.c.id)
                .where(ctx.job_images.c.job_id == row["id"])
                .limit(1)
            ).first()
            if not exists:
                conn.execute(
                    ctx.job_images.insert().values(
                        id=str(
                            uuid.uuid5(uuid.NAMESPACE_URL, "ninja-hub:" + row["id"])
                        ),
                        job_id=row["id"],
                        position=0,
                        image_file=row["image_file"],
                        stored_at=row["finished_at"],
                        image_width=row["image_width"],
                        image_height=row["image_height"],
                        image_format=row["image_format"],
                        image_bytes=row["image_bytes"],
                        image_sha256=row["image_sha256"],
                    )
                )


def now(ctx):
    return datetime.now(timezone.utc).isoformat()


def query_account(ctx, account_id):
    with ctx.engine.connect() as c:
        row = (
            c.execute(select(ctx.accounts).where(ctx.accounts.c.id == account_id))
            .mappings()
            .first()
        )
        return dict(row) if row else None
