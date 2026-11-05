from collections import defaultdict
from datetime import timedelta
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select


def index(ctx):
    return FileResponse(
        ctx.ROOT / "frontend" / "index.html", headers={"Cache-Control": "no-store"}
    )


def queue_metrics(ctx):
    with ctx.engine.connect() as conn:
        counts = dict(
            conn.execute(
                select(ctx.jobs.c.status, func.count()).group_by(ctx.jobs.c.status)
            ).all()
        )
        accounts_data = [
            dict(row) for row in conn.execute(select(ctx.accounts)).mappings().all()
        ]
    with ctx.queue_guard:
        per = {
            a["id"]: {
                "name": a["name"],
                "active": ctx.active_per_account[a["id"]],
                "configured_limit": a.get("parallel_limit") or 2,
                "effective_limit": min(
                    a.get("parallel_limit") or 2, ctx.setting("gateway_max_tabs")
                ),
                "cooldown_until": a.get("cooldown_until"),
            }
            for a in accounts_data
            if not a["archived_at"]
        }
        running = len(ctx.active_jobs)
    with ctx.engine.connect() as conn:
        samples = [
            dict(r)
            for r in conn.execute(
                select(ctx.jobs.c.started_at, ctx.jobs.c.finished_at)
                .where(
                    ctx.jobs.c.status.in_(["completed", "partial"]),
                    ctx.jobs.c.started_at.is_not(None),
                    ctx.jobs.c.finished_at.is_not(None),
                )
                .order_by(ctx.jobs.c.finished_at.desc())
                .limit(250)
            ).mappings()
        ]
        recent_images = conn.execute(
            select(func.count())
            .select_from(
                ctx.job_images.join(ctx.jobs, ctx.jobs.c.id == ctx.job_images.c.job_id)
            )
            .where(
                ctx.jobs.c.finished_at
                >= (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            )
        ).scalar_one()
    latencies = []
    for r in samples:
        try:
            dt = (
                datetime.fromisoformat(r["finished_at"])
                - datetime.fromisoformat(r["started_at"])
            ).total_seconds()
            if 0 <= dt <= 86400:
                latencies.append(dt)
        except (ValueError, TypeError):
            pass
    latencies.sort()

    def pct(x):
        return (
            round(
                latencies[
                    min(len(latencies) - 1, max(0, int((len(latencies) - 1) * x)))
                ],
                1,
            )
            if latencies
            else None
        )

    return {
        "queued": counts.get("queued", 0),
        "running": running,
        "completed": counts.get("completed", 0),
        "partial": counts.get("partial", 0),
        "unknown": counts.get("unknown", 0),
        "max_global_inflight": ctx.setting("global_max_inflight"),
        "gateway_tabs_per_account": ctx.setting("gateway_max_tabs"),
        "pending_capacity": ctx.setting("max_pending_jobs"),
        "max_images_per_job": ctx.setting("max_batch_images"),
        "dispatch_paused": bool(ctx.setting("dispatch_paused")),
        "accounts": per,
        "observed_images_last_hour": recent_images,
        "measured_completed_job_samples": len(latencies),
        "observed_job_p50_sec": pct(0.5),
        "observed_job_p95_sec": pct(0.95),
        "notice": "Queue limits do not imply the provider will allow this throughput. No automatic account rotation or quota bypass.",
    }


def analytics(ctx, hours: int = 5):
    if hours not in (5, 24, 168, 720):
        raise HTTPException(422, "hours must be 5, 24, 168 or 720")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    cut = start.isoformat()
    with ctx.engine.connect() as conn:
        recent = [
            dict(r)
            for r in conn.execute(
                select(
                    ctx.jobs.c.id,
                    ctx.jobs.c.account_id,
                    ctx.jobs.c.status,
                    ctx.jobs.c.n,
                    ctx.jobs.c.created_at,
                    ctx.jobs.c.started_at,
                    ctx.jobs.c.finished_at,
                    ctx.jobs.c.error,
                )
                .where(or_(ctx.jobs.c.created_at >= cut, ctx.jobs.c.finished_at >= cut))
                .order_by(ctx.jobs.c.created_at.desc())
            ).mappings()
        ]
        archived = {
            r["id"]: r["name"]
            for r in conn.execute(
                select(ctx.accounts.c.id, ctx.accounts.c.name)
            ).mappings()
        }
        ids = [r["id"] for r in recent]
        image_count_by_job = {}
        image_events = []
        for batch_start in range(0, len(ids), 300):
            group = ids[batch_start : batch_start + 300]
            for jid, stored_at, finished_at in conn.execute(
                select(
                    ctx.job_images.c.job_id,
                    ctx.job_images.c.stored_at,
                    ctx.jobs.c.finished_at,
                )
                .join(ctx.jobs, ctx.jobs.c.id == ctx.job_images.c.job_id)
                .where(ctx.job_images.c.job_id.in_(group))
            ):
                event_time = stored_at or finished_at
                if not event_time or not cut <= event_time <= end.isoformat():
                    continue
                image_count_by_job[jid] = image_count_by_job.get(jid, 0) + 1
                image_events.append(event_time)
        oldest = conn.execute(
            select(ctx.jobs.c.created_at)
            .where(ctx.jobs.c.status == "queued")
            .order_by(ctx.jobs.c.created_at)
            .limit(1)
        ).scalar()
    buckets = hours if hours <= 24 else 28 if hours == 168 else 30
    bucket_seconds = hours * 3600 / buckets
    graph = [
        {
            "index": i,
            "time": (start + timedelta(seconds=i * bucket_seconds)).isoformat(),
            "images": 0,
            "completed_jobs": 0,
            "errors": 0,
        }
        for i in range(buckets)
    ]
    for timestamp in image_events:
        try:
            slot = int(
                (datetime.fromisoformat(timestamp) - start).total_seconds()
                / bucket_seconds
            )
            if 0 <= slot < buckets:
                graph[slot]["images"] += 1
        except (TypeError, ValueError):
            pass
    account_summary = {}
    statuses = defaultdict(int)
    durations = []
    total_images = 0
    expected_images = 0
    for job in recent:
        statuses[job["status"]] += 1
        done = image_count_by_job.get(job["id"], 0)
        total_images += done
        expected_images += job["n"] or 1
        aid = job["account_id"]
        a = account_summary.setdefault(
            aid,
            {
                "account_id": aid,
                "name": archived.get(aid, "Removed account"),
                "jobs": 0,
                "images": 0,
                "failed": 0,
                "partial": 0,
                "unknown": 0,
                "rate_limited": 0,
            },
        )
        a["jobs"] += 1
        a["images"] += done
        if job["status"] in ("failed", "partial", "unknown", "rate_limited"):
            a[job["status"]] += 1
        timestamp = job["finished_at"]
        if timestamp:
            try:
                when = datetime.fromisoformat(timestamp)
                slot = int((when - start).total_seconds() / bucket_seconds)
                if 0 <= slot < buckets:
                    if job["status"] == "completed":
                        graph[slot]["completed_jobs"] += 1
                    if job["status"] in ("failed", "unknown", "rate_limited"):
                        graph[slot]["errors"] += 1
            except (ValueError, TypeError):
                pass
            if job["started_at"]:
                try:
                    duration = (
                        datetime.fromisoformat(timestamp)
                        - datetime.fromisoformat(job["started_at"])
                    ).total_seconds()
                    if 0 <= duration <= 86400:
                        durations.append(duration)
                except (ValueError, TypeError):
                    pass
    durations.sort()
    percentile = lambda q: (
        round(durations[min(len(durations) - 1, int((len(durations) - 1) * q))], 1)
        if durations
        else None
    )
    age = None
    if oldest:
        try:
            age = max(
                0, round((end - datetime.fromisoformat(oldest)).total_seconds() / 60, 1)
            )
        except ValueError:
            pass
    target_images = ctx.setting("target_images")
    target_hours = ctx.setting("target_window_hours")
    completed_images_per_hour = round(total_images / hours, 2)
    target_rate = target_images / target_hours
    return {
        "hours": hours,
        "window_start": cut,
        "window_end": end.isoformat(),
        "images": total_images,
        "requested_images": expected_images,
        "jobs": len(recent),
        "status_counts": dict(statuses),
        "throughput_images_per_hour": completed_images_per_hour,
        "target_window_projection": round(completed_images_per_hour * target_hours, 1),
        "target_images": target_images,
        "target_window_hours": target_hours,
        "target_rate_per_hour": round(target_rate, 2),
        "target_progress_percent": round(
            min(100, completed_images_per_hour / target_rate * 100), 1
        ),
        "p50_sec": percentile(0.5),
        "p95_sec": percentile(0.95),
        "queued_oldest_age_min": age,
        "series": graph,
        "accounts": sorted(
            account_summary.values(), key=lambda x: (-x["images"], x["name"])
        ),
        "recent_failures": [
            {
                "id": r["id"],
                "account": archived.get(r["account_id"], "Unknown"),
                "status": r["status"],
                "error": (r["error"] or "")[:180],
                "time": r["finished_at"],
            }
            for r in recent
            if r["status"] in ("failed", "unknown", "partial", "rate_limited")
        ][:12],
        "note": "Jobs created or finished in the selected window; images counted at stored_at (legacy files fall back to job finish time). Forecast is descriptive, not predictive.",
    }
