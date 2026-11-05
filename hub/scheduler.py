import sys
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
from sqlalchemy import select, update, func
from .gateway import request_generation, ResponseBudgetError


class ProviderLimit(Exception):
    pass


# Backoff for restarting a crashed browser worker. The first retry is quick, the rest
# slow down so a broken install (no Chrome, bad gateway patch) cannot spin tightly. After
# MAX_RESTART_ATTEMPTS the supervisor stops and waits for an explicit operator Start.
RESTART_BACKOFF_SEC = (30, 120, 600, 1800)
MAX_RESTART_ATTEMPTS = 8


def execute_job(ctx, job_id, acc, prompt, aspect, quality_preference, n=1):
    """Run once. No automatic replay after uncertain upstream outcomes."""
    try:
        if ctx.process_state(acc) != "running" or not ctx.upstream_probe(acc):
            # Nothing was submitted upstream, so returning the job to the queue is safe.
            # Hard-failing here silently dropped a job the provider never saw.
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id, ctx.jobs.c.status == "running")
                    .values(status="queued", started_at=None)
                )
            return
        if quality_preference == "best":
            prompt += "\n\nProduction guidance: preserve requested details faithfully; aim for clean edges, intentional composition, readable text when requested, and a polished final result. Do not add unrelated objects."
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect,
            "n": n,
            "response_format": "url",
        }
        try:
            r = request_generation(
                ctx.upstream(acc) + "/v1/images/generations",
                acc["api_secret"],
                payload,
                n,
            )
        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            ResponseBudgetError,
        ) as e:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(
                        status="unknown",
                        error="Response lost after request submission; upstream outcome unknown. Check manually. Do not auto-retry.",
                        finished_at=ctx.now(),
                    )
                )
            return
        if r.status_code >= 500:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(
                        status="unknown",
                        error="Gateway server failure after submission; outcome unknown. Check manually before retry.",
                        finished_at=ctx.now(),
                    )
                )
            return
        if r.status_code >= 400:
            try:
                error = (r.data or {}).get("error", {})
                detail = (
                    error.get("message", "Gateway rejected image request")
                    if isinstance(error, dict)
                    else str(error)
                )
                kind = error.get("error_kind", "") if isinstance(error, dict) else ""
            except Exception:
                detail = f"Gateway HTTP {r.status_code}"
                kind = ""
            if r.status_code == 429 or kind == "rate_limit":
                retry_after = r.headers.get("retry-after", "")
                try:
                    extra_seconds = max(0, int(retry_after))
                except (ValueError, TypeError):
                    try:
                        extra_seconds = max(
                            0,
                            (
                                parsedate_to_datetime(retry_after).astimezone(
                                    timezone.utc
                                )
                                - datetime.now(timezone.utc)
                            ).total_seconds(),
                        )
                    except (ValueError, TypeError, OverflowError):
                        extra_seconds = 0
                cool_seconds = max(
                    ctx.setting("provider_cooldown_minutes") * 60, extra_seconds
                )
                until = (
                    datetime.now(timezone.utc) + timedelta(seconds=cool_seconds)
                ).isoformat()
                with ctx.engine.begin() as conn:
                    conn.execute(
                        update(ctx.accounts)
                        .where(ctx.accounts.c.id == acc["id"])
                        .values(cooldown_until=until)
                    )
                raise ctx.ProviderLimit(
                    f"Provider rate-limited this account; paused until {until}. No automatic retry."
                )
            raise RuntimeError(str(detail)[:250])
        try:
            response_body = r.data
            if not isinstance(response_body, dict):
                raise ValueError("Invalid gateway response")
        except ValueError:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(
                        status="unknown",
                        error="Gateway response was unreadable after successful request; outcome is unknown.",
                        finished_at=ctx.now(),
                    )
                )
            return
        stored, save_error = ctx.record_image_batch(
            response_body, job_id, acc["port"], n
        )
        status = (
            "completed"
            if len(stored) == n and (not save_error)
            else "partial" if stored else "failed"
        )
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(
                    status=status,
                    finished_at=ctx.now(),
                    error=save_error
                    or (
                        f"Only {len(stored)} of {n} requested images returned. No automatic replay."
                        if len(stored) < n
                        else None
                    ),
                )
            )
    except ctx.ProviderLimit as e:
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(
                    status="rate_limited", error=str(e)[:350], finished_at=ctx.now()
                )
            )
    except Exception as e:
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(status="failed", error=str(e)[:350], finished_at=ctx.now())
            )
    finally:
        with ctx.queue_guard:
            ctx.active_jobs.discard(job_id)
            ctx.active_per_account[acc["id"]] = max(
                0, ctx.active_per_account[acc["id"]] - 1
            )
            ctx.queue_wakeup.set()
        # A finished job counts as recent activity: do not reap the browser in the same
        # minute the account just produced an image.
        ctx.touch_account(acc["id"])


def _candidate_jobs(ctx):
    """Select round-robin across accounts (not oldest 100 jobs from one account)."""
    with ctx.engine.connect() as conn:
        ranked = (
            select(
                ctx.jobs,
                func.row_number()
                .over(
                    partition_by=ctx.jobs.c.account_id,
                    order_by=(ctx.jobs.c.created_at, ctx.jobs.c.id),
                )
                .label("queue_rank"),
            )
            .where(ctx.jobs.c.status == "queued")
            .subquery()
        )
        rows = (
            conn.execute(
                select(ranked)
                .where(ranked.c.queue_rank <= ctx.setting("global_max_inflight"))
                .order_by(ranked.c.created_at, ranked.c.id)
            )
            .mappings()
            .all()
        )
        accs = {r["id"]: dict(r) for r in conn.execute(select(ctx.accounts)).mappings()}
    by_acc = defaultdict(deque)
    for row in rows:
        if row["account_id"] in accs and (not accs[row["account_id"]]["archived_at"]):
            by_acc[row["account_id"]].append(dict(row))
    return (by_acc, accs)


def _refresh_health(ctx):
    """Probe every live worker OUTSIDE the queue lock, in parallel.

    Sequential 0.6 s probes added up to multi-second stalls with many accounts; a small
    pool keeps the snapshot fresh for a 10-account box.
    """
    with ctx.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(select(ctx.accounts)).mappings()]
    snapshot = {}
    targets = []
    for acc in rows:
        if acc["archived_at"]:
            continue
        if ctx.process_state(acc) != "running":
            snapshot[acc["id"]] = False
            continue
        snapshot[acc["id"]] = False
        targets.append(acc)

    def probe(acc):
        try:
            return acc["id"], bool(ctx.upstream_probe(acc))
        except Exception:
            return acc["id"], False

    if targets:
        try:
            for aid, healthy in ctx.health_pool.map(probe, targets, timeout=6):
                snapshot[aid] = healthy
        except Exception:
            # Timeout: keep the fail-closed snapshot (False) for anything unanswered.
            pass
    with ctx.queue_guard:
        ctx.health_cache = snapshot


def _supervise_workers(ctx):
    """Restart autostart workers that died, with backoff. Operator stops are respected."""
    if ctx.queue_stop.is_set() or not ctx.runtime_installed():
        return 0
    with ctx.proc_guard:
        dead = {aid for aid, proc in ctx.processes.items() if proc.poll() is not None}
    for aid in dead:
        with ctx.proc_guard:
            ctx.processes.pop(aid, None)
            handle = ctx.log_handles.pop(aid, None)
        if handle:
            try:
                handle.close()
            except OSError:
                pass
    with ctx.engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(ctx.accounts).where(ctx.accounts.c.autostart == 1)
            ).mappings()
        ]
    now = time.monotonic()
    restarted = 0
    for acc in rows:
        aid = acc["id"]
        if acc["archived_at"] or aid in ctx.suppressed_workers:
            continue
        process = ctx.processes.get(aid)
        if process is not None and process.poll() is None:
            ctx.worker_restart_state.pop(aid, None)
            continue
        state = ctx.worker_restart_state.get(aid)
        if state and now < state["next"]:
            continue
        attempts = state["attempts"] if state else 0
        if attempts >= MAX_RESTART_ATTEMPTS:
            # Stop the automatic retries. The account stays stopped and visible; a manual
            # Start clears this state and re-arms supervision.
            if not state.get("exhausted"):
                state["exhausted"] = True
                print(
                    f"[scheduler] worker {aid} failed to start {attempts} times; "
                    "automatic restart stopped, Start it manually after fixing the install.",
                    file=sys.stderr,
                )
            continue
        delay = RESTART_BACKOFF_SEC[min(attempts, len(RESTART_BACKOFF_SEC) - 1)]
        ctx.worker_restart_state[aid] = {"attempts": attempts + 1, "next": now + delay}
        ctx.start_worker_async(acc)
        restarted += 1
    return restarted


def _wake_for_queue(ctx):
    """Start the browser for accounts that have queued work but no live worker.

    This is what makes on-demand workers possible: an idle-reaped account costs nothing
    until a job arrives, and then it is brought back automatically. An account the operator
    stopped explicitly is NOT woken (the job waits, visible in the queue).
    """
    with ctx.engine.connect() as conn:
        pending = dict(
            conn.execute(
                select(ctx.jobs.c.account_id, func.count())
                .where(ctx.jobs.c.status == "queued")
                .group_by(ctx.jobs.c.account_id)
            ).all()
        )
        if not pending:
            return 0
        accounts = {
            r["id"]: dict(r) for r in conn.execute(select(ctx.accounts)).mappings()
        }
    with ctx.proc_guard:
        alive = {aid for aid, proc in ctx.processes.items() if proc.poll() is None}
    woke = 0
    for aid in pending:
        if aid in alive:
            continue
        acc = accounts.get(aid)
        if not acc or acc["archived_at"] or not acc.get("dispatch_enabled", 1):
            continue
        if aid in ctx.suppressed_workers and aid not in ctx.idle_stopped:
            continue
        if ctx.wake_worker_async(acc):
            woke += 1
    return woke


def _reap_idle_workers(ctx):
    """Stop browsers that have no work, so idle accounts do not hold RAM forever."""
    minutes = int(ctx.setting("idle_worker_shutdown_minutes"))
    if minutes <= 0:
        return 0
    timeout = minutes * 60
    now = time.monotonic()
    with ctx.engine.connect() as conn:
        queued = dict(
            conn.execute(
                select(ctx.jobs.c.account_id, func.count())
                .where(ctx.jobs.c.status == "queued")
                .group_by(ctx.jobs.c.account_id)
            ).all()
        )
    with ctx.proc_guard:
        running = [aid for aid, proc in ctx.processes.items() if proc.poll() is None]
    reaped = 0
    for aid in running:
        if ctx.active_per_account.get(aid, 0) > 0:
            continue
        if queued.get(aid):
            continue
        last = ctx.account_activity.get(aid, now)
        if now - last < timeout:
            continue
        ctx.stop_worker(aid)
        # A reaped worker is not an operator stop: convert the suppression into an idle
        # marker so the next queued job or login page can wake it again.
        ctx.suppressed_workers.discard(aid)
        ctx.idle_stopped.add(aid)
        reaped += 1
        print(
            f"[scheduler] reaped idle worker {aid} after {minutes} min without work",
            file=sys.stderr,
        )
    return reaped


def _dispatch_available(ctx):
    with ctx.queue_guard:
        free = ctx.setting("global_max_inflight") - len(ctx.active_jobs)
        if ctx.setting("dispatch_paused"):
            return 0
        if free <= 0:
            return 0
        by_acc, accs = ctx._candidate_jobs()
        ids = list(by_acc)
        if not ids:
            return 0
        if ctx.last_served_account in ids:
            at = ids.index(ctx.last_served_account)
            ids = ids[at + 1 :] + ids[: at + 1]
        submitted = 0
        eligible = {}
        for aid in ids:
            acc = accs[aid]
            parallel = min(
                max(1, int(acc.get("parallel_limit") or 1)),
                ctx.setting("gateway_max_tabs"),
            )
            remaining = parallel - ctx.active_per_account[aid]
            if remaining <= 0:
                continue
            cooldown = acc.get("cooldown_until")
            if cooldown:
                try:
                    if datetime.fromisoformat(cooldown) > datetime.now(timezone.utc):
                        continue
                except ValueError:
                    continue
            if not acc.get("dispatch_enabled", 1):
                continue
            if not ctx.health_cache.get(aid):
                continue
            eligible[aid] = remaining
        while free > 0 and eligible:
            progressed = False
            for aid in list(ids):
                if aid not in eligible or free <= 0:
                    continue
                if not by_acc[aid] or eligible[aid] <= 0:
                    eligible.pop(aid, None)
                    continue
                row = by_acc[aid].popleft()
                jid = row["id"]
                acc = accs[aid]
                with ctx.engine.begin() as conn:
                    changed = conn.execute(
                        update(ctx.jobs)
                        .where(ctx.jobs.c.id == jid, ctx.jobs.c.status == "queued")
                        .values(status="running", started_at=ctx.now())
                    ).rowcount
                if not changed:
                    continue
                ctx.active_jobs.add(jid)
                ctx.active_per_account[aid] += 1
                submitted += 1
                free -= 1
                eligible[aid] -= 1
                ctx.last_served_account = aid
                ctx.touch_account(aid)
                progressed = True
                try:
                    ctx.pool.submit(
                        ctx.execute_job,
                        jid,
                        acc,
                        row["prompt"],
                        row["aspect_ratio"],
                        row["quality_preference"],
                        row.get("n") or 1,
                    )
                except RuntimeError:
                    ctx.active_jobs.discard(jid)
                    ctx.active_per_account[aid] -= 1
                    with ctx.engine.begin() as conn:
                        conn.execute(
                            update(ctx.jobs)
                            .where(ctx.jobs.c.id == jid)
                            .values(status="queued", started_at=None)
                        )
                    raise
            if not progressed:
                break
        return submitted


def scheduler_loop(ctx):
    while not ctx.queue_stop.is_set():
        try:
            ctx._supervise_workers()
            ctx._wake_for_queue()
            ctx._reap_idle_workers()
            ctx._refresh_health()
            ctx._dispatch_available()
        except Exception as exc:
            print("[scheduler] dispatch error:", str(exc)[:300], file=sys.stderr)
        ctx.queue_wakeup.wait(2.0)
        ctx.queue_wakeup.clear()


def start_scheduler(ctx):
    ctx.queue_stop.clear()
    if not ctx.dispatcher_thread or not ctx.dispatcher_thread.is_alive():
        ctx.dispatcher_thread = threading.Thread(
            target=ctx.scheduler_loop, daemon=True, name="fair-image-dispatcher"
        )
        ctx.dispatcher_thread.start()


def stop_scheduler(ctx):
    ctx.queue_stop.set()
    ctx.queue_wakeup.set()
    if ctx.dispatcher_thread and ctx.dispatcher_thread.is_alive():
        ctx.dispatcher_thread.join(timeout=3)


def startup_workers(ctx):
    ctx.start_scheduler()
    if not ctx.runtime_installed():
        return
    with ctx.engine.connect() as c:
        rows = [
            dict(r)
            for r in c.execute(
                select(ctx.accounts).where(ctx.accounts.c.autostart == 1)
            ).mappings()
        ]
    for acc in rows:
        if not acc["archived_at"]:
            ctx.start_worker_async(acc)


def shutdown_workers(ctx):
    ctx.stop_scheduler()
    ctx.pool.shutdown(wait=True, cancel_futures=False)
    ctx.status_pool.shutdown(wait=True, cancel_futures=False)
    ctx.health_pool.shutdown(wait=True, cancel_futures=False)
    with ctx.proc_guard:
        ids = list(ctx.processes)
    for aid in ids:
        ctx.stop_worker(aid)
