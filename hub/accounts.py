import re
import secrets
import shutil
import threading
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select, update
from .schemas import AccountBody, AccountUpdate, ConfirmRemove


def overview(ctx):
    with ctx.engine.connect() as c:
        acc_count = c.execute(
            select(func.count())
            .select_from(ctx.accounts)
            .where(ctx.accounts.c.archived_at.is_(None))
        ).scalar_one()
        total_jobs = c.execute(select(func.count()).select_from(ctx.jobs)).scalar_one()
        complete = c.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(ctx.jobs.c.status == "completed")
        ).scalar_one()
        queued = c.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(ctx.jobs.c.status.in_(["queued", "running"]))
        ).scalar_one()
    return {
        "accounts": acc_count,
        "jobs": total_jobs,
        "completed": complete,
        "active": queued,
        "runtime_installed": ctx.runtime_installed(),
    }


def list_accounts(ctx, include_archived: bool = True):
    stmt = select(ctx.accounts).order_by(ctx.accounts.c.created_at)
    if not include_archived:
        stmt = stmt.where(ctx.accounts.c.archived_at.is_(None))
    with ctx.engine.connect() as c:
        rows = [dict(r) for r in c.execute(stmt).mappings()]
    return list(ctx.status_pool.map(ctx.safe_account, rows))


def add_account(ctx, body: AccountBody):
    if body.plan_label not in {"unknown", "free", "plus", "pro", "other"}:
        raise HTTPException(422, "Invalid manual plan label.")
    aid = str(uuid.uuid4())
    acc = {
        "id": aid,
        "name": body.name.strip(),
        "email_label": body.email_label.strip(),
        "port": ctx.allocate_port(),
        "api_secret": secrets.token_urlsafe(38),
        "autostart": int(body.autostart),
        "archived_at": None,
        "plan_label": body.plan_label,
        "plan_checked_at": ctx.now() if body.plan_label != "unknown" else None,
        "created_at": ctx.now(),
        "parallel_limit": body.parallel_limit,
        "cooldown_until": None,
        "dispatch_enabled": 1,
    }
    if not acc["name"]:
        raise HTTPException(422, "Account name required.")
    with ctx.engine.begin() as c:
        c.execute(ctx.accounts.insert().values(**acc))
    (ctx.DATA / "accounts" / aid).mkdir(parents=True, exist_ok=True)
    if acc["autostart"] and ctx.runtime_installed():
        ctx.start_worker_async(acc)
    return ctx.safe_account(acc)


def edit_account(ctx, account_id: str, body: AccountUpdate):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    if acc["archived_at"]:
        raise HTTPException(409, "Restore the account before editing.")
    if not body.name.strip():
        raise HTTPException(422, "Account name required.")
    if body.plan_label not in {"unknown", "free", "plus", "pro", "other"}:
        raise HTTPException(422, "Invalid manual plan label.")
    with ctx.engine.begin() as c:
        c.execute(
            update(ctx.accounts)
            .where(ctx.accounts.c.id == account_id)
            .values(
                name=body.name.strip(),
                email_label=body.email_label.strip(),
                autostart=int(body.autostart),
                plan_label=body.plan_label,
                plan_checked_at=ctx.now() if body.plan_label != "unknown" else None,
                parallel_limit=body.parallel_limit,
            )
        )
    return ctx.safe_account(ctx.query_account(account_id))


def start_account(ctx, account_id: str):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    if acc["archived_at"]:
        raise HTTPException(409, "Restore the account before starting it.")
    if not ctx.runtime_installed():
        raise HTTPException(
            409, "Install gateway runtime first: python scripts/prepare_gateway.py"
        )
    try:
        ctx.start_worker(acc)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    ctx.queue_wakeup.set()
    return {"ok": True}


def stop_account(ctx, account_id: str):
    with ctx.account_mutation_locks.setdefault(account_id, threading.RLock()):
        with ctx.queue_guard:
            if not ctx.query_account(account_id):
                raise HTTPException(404, "Account not found.")
            ctx.ensure_no_running_jobs(account_id)
            ctx.stop_worker(account_id)
    ctx.queue_wakeup.set()
    return {"ok": True}


def ensure_no_running_jobs(ctx, account_id):
    with ctx.engine.connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(ctx.jobs.c.account_id == account_id, ctx.jobs.c.status == "running")
        ).scalar_one()
    if n:
        raise HTTPException(
            409,
            f"{n} running job(s): cannot stop browser mid-generation. Wait for completion.",
        )


def ensure_no_active_jobs(ctx, account_id):
    with ctx.engine.connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(ctx.jobs)
            .where(
                ctx.jobs.c.account_id == account_id,
                ctx.jobs.c.status.in_(["queued", "running"]),
            )
        ).scalar_one()
    if n:
        raise HTTPException(
            409, f"{n} active job(s). Wait until they finish before archiving/deleting."
        )


def account_dispatch_mode(ctx, account_id: str, mode: str):
    if mode not in ("pause", "resume"):
        raise HTTPException(422, "Expected pause or resume.")
    with ctx.account_mutation_locks.setdefault(account_id, threading.RLock()):
        acc = ctx.query_account(account_id)
        if not acc:
            raise HTTPException(404, "Account not found.")
        if acc["archived_at"]:
            raise HTTPException(409, "Archived account cannot dispatch.")
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.accounts)
                .where(ctx.accounts.c.id == account_id)
                .values(dispatch_enabled=int(mode == "resume"))
            )
        ctx.record_audit("account.dispatch", {"account_id": account_id, "mode": mode})
    ctx.queue_wakeup.set()
    return {
        "ok": True,
        "dispatch_enabled": bool(mode == "resume"),
        "note": "Only new jobs are affected. Running jobs continue. Cooldowns are never bypassed.",
    }


def archive_account(ctx, account_id: str):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    with ctx.account_mutation_locks.setdefault(account_id, threading.RLock()):
        with ctx.queue_guard:
            ctx.ensure_no_active_jobs(account_id)
            ctx.stop_worker(account_id)
            with ctx.engine.begin() as c:
                c.execute(
                    update(ctx.accounts)
                    .where(ctx.accounts.c.id == account_id)
                    .values(archived_at=ctx.now(), autostart=0)
                )
    return {
        "ok": True,
        "message": "Archived: worker stopped, jobs and images retained.",
    }


def restore_account(ctx, account_id: str):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    with ctx.engine.begin() as c:
        c.execute(
            update(ctx.accounts)
            .where(ctx.accounts.c.id == account_id)
            .values(archived_at=None, autostart=0)
        )
    ctx.queue_wakeup.set()
    return {
        "ok": True,
        "message": "Restored. Start the worker and verify login manually.",
    }


def permanently_remove_account(ctx, account_id: str, body: ConfirmRemove):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    if not acc["archived_at"]:
        raise HTTPException(409, "Archive account first.")
    if body.confirmation != "DELETE " + acc["name"]:
        raise HTTPException(
            422, "Confirmation must exactly match DELETE <account name>."
        )
    with ctx.queue_guard:
        ctx.ensure_no_active_jobs(account_id)
        ctx.stop_worker(account_id)
    if str(uuid.UUID(account_id)) != account_id:
        raise HTTPException(422, "Invalid account ID.")
    with ctx.engine.connect() as c:
        images = [
            r[0]
            for r in c.execute(
                select(ctx.job_images.c.image_file)
                .join(ctx.jobs, ctx.jobs.c.id == ctx.job_images.c.job_id)
                .where(ctx.jobs.c.account_id == account_id)
            )
        ]
        images.extend(
            (
                r[0]
                for r in c.execute(
                    select(ctx.jobs.c.image_file).where(
                        ctx.jobs.c.account_id == account_id
                    )
                )
                if r[0]
            )
        )
    with ctx.engine.begin() as c:
        c.execute(
            ctx.job_images.delete().where(
                ctx.job_images.c.job_id.in_(
                    select(ctx.jobs.c.id).where(ctx.jobs.c.account_id == account_id)
                )
            )
        )
        c.execute(ctx.jobs.delete().where(ctx.jobs.c.account_id == account_id))
        c.execute(ctx.accounts.delete().where(ctx.accounts.c.id == account_id))
    for filename in images:
        if re.fullmatch("[a-f0-9-]{36}(?:-[0-9]{1,2})?\\.(png|jpg|webp)", filename):
            (ctx.DATA / "gallery" / filename).unlink(missing_ok=True)
    worker_dir = ctx.DATA / "accounts" / account_id
    if worker_dir.exists():
        shutil.rmtree(worker_dir)
    return {
        "ok": True,
        "message": "Account, job records, gallery files and local session directory removed. This is not a secure disk wipe.",
    }


def deprecated_delete_account(ctx, account_id: str):
    raise HTTPException(
        410,
        "Use Archive from the dashboard, then Delete Forever with confirmation if needed.",
    )


def worker_logs(ctx, account_id: str):
    if not ctx.query_account(account_id):
        raise HTTPException(404, "Account not found.")
    path = ctx.DATA / "accounts" / account_id / "worker.log"
    if not path.exists():
        return {"logs": "No logs yet."}
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 10000))
        text = f.read().decode("utf-8", errors="replace")
    text = re.sub("(?i)(bearer\\s+)[^\\s]+", "\\1[REDACTED]", text)
    text = re.sub(
        "(?i)(password|token|secret|cookie)\\s*[=:]\\s*[^\\s,]+", "\\1=[REDACTED]", text
    )
    return {"logs": text[-8000:]}
