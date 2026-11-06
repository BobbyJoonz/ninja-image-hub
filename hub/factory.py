"""Application composition. Domain modules receive an explicit per-app runtime."""

import mimetypes
from contextlib import asynccontextmanager
from functools import partial
from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from .runtime import create_runtime
from .limits import RequestBodyLimit
from . import (
    control,
    database,
    security,
    workers,
    telemetry,
    accounts,
    login,
    storage,
    scheduler,
    jobs,
    system,
)
import os
import threading
from . import delivery


# The Windows registry can map .js to text/plain (HKCU\Software\Classes\.js). Browsers
# refuse to execute an ES module served with a non-JS MIME type, which leaves the panel
# stuck on the sign-in shell while every click silently does nothing. Pin the mapping so
# the dashboard boots on a fresh Windows host without touching the registry.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/json", ".json")


def create_app():
    ctx = create_runtime()
    ctx.preview_slots = threading.BoundedSemaphore(2)
    ctx.export_slots = threading.BoundedSemaphore(2)
    for name in (
        "cfg_int",
        "setting",
        "record_audit",
        "get_control",
        "update_control",
        "read_control_audit",
    ):
        setattr(ctx, name, partial(getattr(control, name), ctx))
    for name in ("now", "query_account"):
        setattr(ctx, name, partial(getattr(database, name), ctx))
    for name in (
        "require_admin",
        "csrf_admin",
        "require_agent",
        "login",
        "me",
        "logout",
    ):
        setattr(ctx, name, partial(getattr(security, name), ctx))
    for name in (
        "safe_account",
        "worker_rss_mb",
        "upstream",
        "runtime_installed",
        "listening",
        "port_available",
        "allocate_port",
        "process_state",
        "upstream_probe",
        "session_probe",
        "account_status",
        "ensure_worker_copy",
        "start_worker",
        "stop_worker",
        "start_worker_async",
        "wake_worker_async",
        "reclaim_orphan_worker",
        "touch_account",
    ):
        setattr(ctx, name, partial(getattr(workers, name), ctx))
    for name in ("index", "queue_metrics", "analytics"):
        setattr(ctx, name, partial(getattr(telemetry, name), ctx))
    for name in (
        "overview",
        "list_accounts",
        "add_account",
        "edit_account",
        "start_account",
        "stop_account",
        "ensure_no_running_jobs",
        "ensure_no_active_jobs",
        "account_dispatch_mode",
        "archive_account",
        "restore_account",
        "permanently_remove_account",
        "deprecated_delete_account",
        "worker_logs",
        "auto_login_status",
        "set_auto_login",
        "clear_auto_login",
    ):
        setattr(ctx, name, partial(getattr(accounts, name), ctx))
    for name in (
        "login_page",
        "upstream_login_call",
        "remote_init",
        "remote_shot",
        "remote_click",
        "remote_type",
        "remote_key",
        "remote_nav",
        "remote_save",
    ):
        setattr(ctx, name, partial(getattr(login, name), ctx))
    for name in (
        "write_image_payload",
        "record_image_batch",
        "read_image_file",
        "image_index_download",
        "agent_index_download",
        "image_download",
        "agent_image_download",
    ):
        setattr(ctx, name, partial(getattr(storage, name), ctx))
    for name in (
        "execute_job",
        "_candidate_jobs",
        "_dispatch_available",
        "_refresh_health",
        "_wake_for_queue",
        "_reap_idle_workers",
        "scheduler_loop",
        "start_scheduler",
        "stop_scheduler",
        "startup_workers",
        "shutdown_workers",
        "_supervise_workers",
    ):
        setattr(ctx, name, partial(getattr(scheduler, name), ctx))
    for name in (
        "queue_image",
        "generate",
        "agent_generate",
        "serialize_job",
        "list_jobs",
        "job_detail",
        "agent_job",
        "delete_job",
    ):
        setattr(ctx, name, partial(getattr(jobs, name), ctx))
    for name in ("settings", "health"):
        setattr(ctx, name, partial(getattr(system, name), ctx))
    ctx.ProviderLimit = scheduler.ProviderLimit
    ctx.LOGIN_ACTIONS = {"screenshot", "click", "type", "key", "navigate", "save"}

    @asynccontextmanager
    async def lifespan(app):
        ctx.startup_workers()
        try:
            yield
        finally:
            ctx.shutdown_workers()
            ctx.engine.dispose()

    app = FastAPI(
        title="Ninja Image Hub",
        version="0.6.1",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.hub = ctx
    app.add_middleware(RequestBodyLimit)
    app.add_middleware(
        SessionMiddleware,
        secret_key=ctx.SESSION_SECRET,
        session_cookie="ninja_session",
        same_site="strict",
        https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=28800,
    )
    app.mount(
        "/static", StaticFiles(directory=str(ctx.ROOT / "frontend")), name="static"
    )
    app.get("/api/control", dependencies=[Depends(ctx.require_admin)])(ctx.get_control)
    app.patch("/api/control", dependencies=[Depends(ctx.csrf_admin)])(
        ctx.update_control
    )
    app.get("/api/control/audit", dependencies=[Depends(ctx.require_admin)])(
        ctx.read_control_audit
    )
    app.post("/api/auth/login")(ctx.login)
    app.get("/api/auth/me")(ctx.me)
    app.post("/api/auth/logout", dependencies=[Depends(ctx.csrf_admin)])(ctx.logout)
    app.get("/")(ctx.index)
    app.get("/api/queue", dependencies=[Depends(ctx.require_admin)])(ctx.queue_metrics)
    app.get("/api/analytics", dependencies=[Depends(ctx.require_admin)])(ctx.analytics)
    app.get("/api/overview", dependencies=[Depends(ctx.require_admin)])(ctx.overview)
    app.get("/api/accounts", dependencies=[Depends(ctx.require_admin)])(
        ctx.list_accounts
    )
    app.post("/api/accounts", dependencies=[Depends(ctx.csrf_admin)], status_code=201)(
        ctx.add_account
    )
    app.patch("/api/accounts/{account_id}", dependencies=[Depends(ctx.csrf_admin)])(
        ctx.edit_account
    )
    app.post(
        "/api/accounts/{account_id}/start", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.start_account)
    app.post("/api/accounts/{account_id}/stop", dependencies=[Depends(ctx.csrf_admin)])(
        ctx.stop_account
    )
    app.post(
        "/api/accounts/{account_id}/dispatch/{mode}",
        dependencies=[Depends(ctx.csrf_admin)],
    )(ctx.account_dispatch_mode)
    app.post(
        "/api/accounts/{account_id}/archive", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.archive_account)
    app.post(
        "/api/accounts/{account_id}/restore", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.restore_account)
    app.post(
        "/api/accounts/{account_id}/purge", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.permanently_remove_account)
    app.delete("/api/accounts/{account_id}", dependencies=[Depends(ctx.csrf_admin)])(
        ctx.deprecated_delete_account
    )
    app.get(
        "/api/accounts/{account_id}/logs", dependencies=[Depends(ctx.require_admin)]
    )(ctx.worker_logs)
    app.get(
        "/api/accounts/{account_id}/auto-login",
        dependencies=[Depends(ctx.require_admin)],
    )(ctx.auto_login_status)
    app.put(
        "/api/accounts/{account_id}/auto-login",
        dependencies=[Depends(ctx.csrf_admin)],
    )(ctx.set_auto_login)
    app.delete(
        "/api/accounts/{account_id}/auto-login",
        dependencies=[Depends(ctx.csrf_admin)],
    )(ctx.clear_auto_login)
    app.get(
        "/accounts/{account_id}/login",
        response_class=HTMLResponse,
        dependencies=[Depends(ctx.require_admin)],
    )(ctx.login_page)
    app.get(
        "/api/accounts/{account_id}/remote/init",
        dependencies=[Depends(ctx.require_admin)],
    )(ctx.remote_init)
    app.get(
        "/api/accounts/{account_id}/remote/screenshot",
        dependencies=[Depends(ctx.require_admin)],
    )(ctx.remote_shot)
    app.post(
        "/api/accounts/{account_id}/remote/click",
        dependencies=[Depends(ctx.csrf_admin)],
    )(ctx.remote_click)
    app.post(
        "/api/accounts/{account_id}/remote/type", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.remote_type)
    app.post(
        "/api/accounts/{account_id}/remote/key", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.remote_key)
    app.post(
        "/api/accounts/{account_id}/remote/navigate",
        dependencies=[Depends(ctx.csrf_admin)],
    )(ctx.remote_nav)
    app.post(
        "/api/accounts/{account_id}/remote/save", dependencies=[Depends(ctx.csrf_admin)]
    )(ctx.remote_save)
    app.get(
        "/api/images/{job_id}/{position}", dependencies=[Depends(ctx.require_admin)]
    )(ctx.image_index_download)
    app.get(
        "/v1/images/{job_id}/{position}", dependencies=[Depends(ctx.require_agent)]
    )(ctx.agent_index_download)
    app.get("/api/images/{job_id}", dependencies=[Depends(ctx.require_admin)])(
        ctx.image_download
    )
    app.get("/v1/images/{job_id}", dependencies=[Depends(ctx.require_agent)])(
        ctx.agent_image_download
    )
    app.post("/api/generate", dependencies=[Depends(ctx.csrf_admin)], status_code=202)(
        ctx.generate
    )
    app.post(
        "/v1/images/generations",
        dependencies=[Depends(ctx.require_agent)],
        status_code=202,
    )(ctx.agent_generate)
    app.get("/api/jobs", dependencies=[Depends(ctx.require_admin)])(ctx.list_jobs)
    app.get("/api/jobs/{job_id}", dependencies=[Depends(ctx.require_admin)])(
        ctx.job_detail
    )
    app.get("/v1/jobs/{job_id}", dependencies=[Depends(ctx.require_agent)])(
        ctx.agent_job
    )
    app.delete("/api/jobs/{job_id}", dependencies=[Depends(ctx.csrf_admin)])(
        ctx.delete_job
    )
    app.get("/api/settings", dependencies=[Depends(ctx.require_admin)])(ctx.settings)
    # Unauthenticated liveness/readiness probe on the loopback interface only. It exposes
    # counters and booleans, never paths, secrets or prompts.
    app.get("/api/health")(ctx.health)
    app.get(
        "/api/previews/{job_id}/{position}", dependencies=[Depends(ctx.require_admin)]
    )(partial(delivery.preview_image, ctx))
    app.get("/api/gallery", dependencies=[Depends(ctx.require_admin)])(
        partial(delivery.gallery_page, ctx)
    )
    app.get(
        "/api/exports/jobs/{job_id}.zip", dependencies=[Depends(ctx.require_admin)]
    )(partial(delivery.export_job, ctx))
    app.get("/v1/exports/jobs/{job_id}.zip", dependencies=[Depends(ctx.require_agent)])(
        partial(delivery.export_job, ctx)
    )
    return app
