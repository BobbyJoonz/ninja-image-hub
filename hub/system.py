import os

from . import plans
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import effort


def _disk_free_mb(ctx):
    try:
        return int(shutil.disk_usage(ctx.DATA).free // (1024 * 1024))
    except OSError:
        return None


def _disk_low(ctx):
    free = _disk_free_mb(ctx)
    threshold = int(ctx.setting("min_free_disk_mb") or 0)
    if free is None or threshold <= 0:
        return False
    return free < threshold


def _effort_counts(ctx, hours=24):
    """Recent jobs whose applied reasoning tier was not confirmed at the requested level."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with ctx.engine.connect() as conn:
        below = conn.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(
                ctx.jobs.c.thinking_requested.is_not(None),
                ctx.jobs.c.created_at >= cutoff,
                ctx.jobs.c.thinking_verified == 0,
            )
        ).scalar_one()
        mixed = conn.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(
                ctx.jobs.c.thinking_mixed == 1,
                ctx.jobs.c.created_at >= cutoff,
            )
        ).scalar_one()
    return below, mixed


def settings(ctx):
    return {
        "gateway_installed": ctx.runtime_installed(),
        "gateway_path": str(ctx.VENDOR),
        "data_path": str(ctx.DATA),
        "display_mode": ctx.display_state(),
        "session_supported": True,
        "database_type": (
            "PostgreSQL" if ctx.DB_URL.startswith("postgresql") else "SQLite"
        ),
        "agent_api_enabled": bool(ctx.AGENT_API_KEY),
        "cookie_secure": os.getenv("COOKIE_SECURE", "false") == "true",
        "user_runtime": "Node.js via localhost process, one process per account",
        "max_running_accounts": ctx.setting("max_running_accounts"),
        "global_max_inflight": ctx.setting("global_max_inflight"),
        "gateway_tabs_per_account": ctx.setting("gateway_max_tabs"),
        "max_pending_jobs": ctx.setting("max_pending_jobs"),
        "max_batch_images": ctx.setting("max_batch_images"),
        "provider_cooldown_minutes": ctx.setting("provider_cooldown_minutes"),
        "idle_worker_shutdown_minutes": ctx.setting("idle_worker_shutdown_minutes"),
        "worker_policy": (
            "on-demand (idle browsers are stopped)"
            if ctx.setting("idle_worker_shutdown_minutes") > 0
            else "always-on (idle browsers stay running)"
        ),
        "dispatch_paused": bool(ctx.setting("dispatch_paused")),
        "preview_retention_days": ctx.setting("preview_retention_days"),
        "export_retention_hours": ctx.setting("export_retention_hours"),
        "worker_log_max_mb": ctx.setting("worker_log_max_mb"),
        "min_free_disk_mb": ctx.setting("min_free_disk_mb"),
        "thinking_effort": ctx.setting("thinking_effort"),
        "thinking_effort_label": effort.effort_label(ctx.setting("thinking_effort")),
        "effort_below_requested_24h": _effort_counts(ctx)[0],
        "effort_mixed_24h": _effort_counts(ctx)[1],
        "disk_free_mb": _disk_free_mb(ctx),
        "disk_low": _disk_low(ctx),
        "terms_warning": "Unofficial ChatGPT UI automation may violate OpenAI Terms and cause account suspension.",
        "vendor_url": "https://github.com/stufently/gpt-web-gateway",
        "setup_command": "python scripts/prepare_gateway.py",
    }


def health(ctx):
    """Unauthenticated readiness summary for monitoring and the first-run checklist.

    Deliberately limited to counters and booleans: no filesystem paths, prompt text,
    account labels, API keys or cookies.
    """
    with ctx.engine.connect() as conn:
        counts = dict(
            conn.execute(
                select(ctx.jobs.c.status, func.count()).group_by(ctx.jobs.c.status)
            ).all()
        )
        accounts_total = conn.execute(
            select(func.count()).select_from(ctx.accounts)
        ).scalar_one()
        plan_rows = conn.execute(
            select(ctx.accounts.c.plan_label, ctx.accounts.c.plan_meta)
        ).all()
    with ctx.proc_guard:
        alive = sum(1 for worker in ctx.processes.values() if worker.poll() is None)
    disk_free = _disk_free_mb(ctx)
    disk_low = _disk_low(ctx)
    return {
        "ok": True,
        "version": "0.8.0",
        "gateway_installed": ctx.runtime_installed(),
        "accounts": accounts_total,
        "running_workers": alive,
        "idle_workers": len(ctx.idle_stopped),
        "worker_policy": (
            "on-demand" if ctx.setting("idle_worker_shutdown_minutes") > 0 else "always-on"
        ),
        "dispatch_paused": bool(ctx.setting("dispatch_paused")),
        "disk_free_mb": disk_free,
        "disk_low": disk_low,
        "accounts_free_plan": sum(
            1 for label, _ in plan_rows if str(label or "").lower() in ("free", "guest")
        ),
        "accounts_plan_expiring": sum(
            1
            for _, meta in plan_rows
            if (summary := plans.summary({"plan_meta": meta}))
            and summary.get("days_left") is not None
            and summary["days_left"] <= 3
        ),
        "unresolved_unknown": ctx.unresolved_unknown(),
        "effort_below_requested_24h": _effort_counts(ctx)[0],
        "effort_mixed_24h": _effort_counts(ctx)[1],
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "partial": counts.get("partial", 0),
        "failed": counts.get("failed", 0),
        "unknown": counts.get("unknown", 0),
        "rate_limited": counts.get("rate_limited", 0),
    }
