import os

from sqlalchemy import func, select


def settings(ctx):
    return {
        "gateway_installed": ctx.runtime_installed(),
        "gateway_path": str(ctx.VENDOR),
        "data_path": str(ctx.DATA),
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
    with ctx.proc_guard:
        alive = sum(1 for worker in ctx.processes.values() if worker.poll() is None)
    return {
        "ok": True,
        "version": "0.6.1",
        "gateway_installed": ctx.runtime_installed(),
        "accounts": accounts_total,
        "running_workers": alive,
        "idle_workers": len(ctx.idle_stopped),
        "worker_policy": (
            "on-demand" if ctx.setting("idle_worker_shutdown_minutes") > 0 else "always-on"
        ),
        "dispatch_paused": bool(ctx.setting("dispatch_paused")),
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "partial": counts.get("partial", 0),
        "failed": counts.get("failed", 0),
        "unknown": counts.get("unknown", 0),
    }
