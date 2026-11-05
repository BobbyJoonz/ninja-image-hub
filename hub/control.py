import json
import os
import uuid
import threading
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select, update
from .schemas import ControlUpdate


def cfg_int(ctx, name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def setting(ctx, key):
    with ctx.config_guard:
        return ctx.config_cache[key]


def record_audit(ctx, action, detail):
    with ctx.engine.begin() as conn:
        conn.execute(
            ctx.control_audit.insert().values(
                id=str(uuid.uuid4()),
                action=action,
                detail=json.dumps(detail, ensure_ascii=False, sort_keys=True),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )


def get_control(ctx):
    with ctx.config_guard:
        options = dict(ctx.config_cache)
    with ctx.proc_guard:
        active = sum((1 for worker in ctx.processes.values() if worker.poll() is None))
    return {
        "config": options,
        "active_workers": active,
        "running_jobs": len(ctx.active_jobs),
        "requires_worker_restart": ["gateway_max_tabs", "max_batch_images"],
        "note": "Gateway configuration changes affect newly started workers. Stop/start idle workers to apply. No provider limit bypass.",
    }


def update_control(ctx, body: ControlUpdate):
    changed = body.model_dump(exclude_unset=True)
    if not changed:
        raise HTTPException(422, "No settings provided.")
    if any((v is None for v in changed.values())):
        raise HTTPException(422, "Settings cannot be null.")
    with ctx.config_guard:
        updates = {key: int(value) for key, value in changed.items()}
        old = {key: ctx.config_cache[key] for key in updates}
        with ctx.engine.begin() as conn:
            for key, value in updates.items():
                conn.execute(
                    update(ctx.control_settings)
                    .where(ctx.control_settings.c.key == key)
                    .values(value=str(value), updated_at=ctx.now())
                )
            conn.execute(
                ctx.control_audit.insert().values(
                    id=str(uuid.uuid4()),
                    action="config.update",
                    detail=json.dumps(
                        {"before": old, "after": updates}, sort_keys=True
                    ),
                    created_at=ctx.now(),
                )
            )
        ctx.config_cache.update(updates)
    ctx.queue_wakeup.set()
    return {
        "ok": True,
        "config": dict(ctx.config_cache),
        "restart_required": sorted(
            set(updates) & {"gateway_max_tabs", "max_batch_images"}
        ),
        "message": "Saved to database. Gateway-level changes need an idle worker restart.",
    }


def read_control_audit(ctx, limit: int = 30):
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(ctx.control_audit)
            .order_by(ctx.control_audit.c.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).mappings()
        return [dict(x) for x in rows]


def initialize_controls(ctx):
    ctx.CONTROL_SCHEMA = {
        "global_max_inflight": (4, 1, 32, "GLOBAL_MAX_INFLIGHT"),
        "gateway_max_tabs": (3, 1, 5, "GATEWAY_MAX_TABS"),
        "max_pending_jobs": (1000, 1, 10000, "MAX_PENDING_JOBS"),
        "max_batch_images": (4, 1, 10, "MAX_BATCH_IMAGES"),
        "provider_cooldown_minutes": (30, 1, 240, "PROVIDER_COOLDOWN_MINUTES"),
        "max_running_accounts": (2, 1, 16, "MAX_RUNNING_ACCOUNTS"),
        "min_free_memory_mb": (600, 0, 65536, "MIN_FREE_MEMORY_MB"),
        "dispatch_paused": (0, 0, 1, None),
        "idle_worker_shutdown_minutes": (20, 0, 1440, "IDLE_WORKER_SHUTDOWN_MINUTES"),
        "target_images": (500, 1, 100000, None),
        "target_window_hours": (5, 1, 24, None),
    }

    ctx.config_guard = threading.RLock()

    ctx.config_cache = {}

    with ctx.engine.begin() as connection:
        present = dict(
            connection.execute(
                select(ctx.control_settings.c.key, ctx.control_settings.c.value)
            ).all()
        )
        for k, (default, lo, hi, envname) in ctx.CONTROL_SCHEMA.items():
            val = (
                int(present[k])
                if k in present and present[k].isdigit()
                else ctx.cfg_int(envname, default, lo, hi) if envname else default
            )
            val = max(lo, min(hi, val))
            ctx.config_cache[k] = val
            if k not in present:
                connection.execute(
                    ctx.control_settings.insert().values(
                        key=k,
                        value=str(val),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
