"""Measure real capacity against a running panel (and optionally submit a live probe).

Read-only by default: prints what the Hub reports about workers, RAM, queue and latency.
With ``--submit --yes`` it sends a small number of real jobs (one per account by default)
and reports measured wall time, queue wait, generation time and sustained throughput.

This is the tool for the "10 accounts" claim: run it against a real panel with real
accounts. It never fabricates numbers — whatever the provider does is what you get.

    python scripts/capacity_report.py --password "$ADMIN_PASSWORD"
    python scripts/capacity_report.py --password ... --submit --yes --accounts 10

The password can also come from NINJA_ADMIN_PASSWORD. Submitting consumes real provider
quota, so it is deliberately double-gated.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required (pip install -r requirements.txt)")

SETTLED = {"completed", "partial", "failed", "unknown", "rate_limited", "canceled"}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _panel import Panel  # noqa: E402


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * q))
    return round(ordered[index], 1)


def summarize(jobs):
    """Aggregate settled job rows. Pure, so it can be unit-tested."""
    counts = {}
    images = 0
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
        images += int(job.get("image_count") or 0)
    return {
        "jobs": len(jobs),
        "settled": sum(counts.get(status, 0) for status in SETTLED),
        "images": images,
        "counts": counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="http://127.0.0.1:8081")
    parser.add_argument("--password", default=os.getenv("NINJA_ADMIN_PASSWORD", ""))
    parser.add_argument("--accounts", type=int, default=0, help="how many accounts to use (0 = all)")
    parser.add_argument("--jobs-per-account", type=int, default=1)
    parser.add_argument("--n", type=int, default=1, help="images per job")
    parser.add_argument("--submit", action="store_true", help="actually queue jobs")
    parser.add_argument("--yes", action="store_true", help="confirm real quota consumption")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="", help="write the JSON snapshot/report to this file")
    args = parser.parse_args()

    if not args.password:
        sys.exit("Provide --password or NINJA_ADMIN_PASSWORD to read the panel.")

    panel = Panel(args.panel, args.password, timeout=60)
    try:
        panel.login()
    except Exception as exc:  # noqa: BLE001 - one clear message for the operator
        sys.exit(f"login failed: {exc}")
    client = panel.client
    headers = panel.headers

    accounts = [a for a in client.get("/api/accounts").json() if not a.get("archived_at")]
    accounts = accounts[: args.accounts or None]
    health = client.get("/api/health").json()
    settings = client.get("/api/settings").json()
    queue = client.get("/api/queue", headers=headers).json()

    snapshot = {
        "panel": args.panel,
        "version": health.get("version"),
        "workers_policy": health.get("worker_policy"),
        "accounts": [
            {
                "name": a["name"],
                "runtime": a["runtime"],
                "session": a["session"],
                "memory_mb": a.get("memory_mb"),
            }
            for a in accounts
        ],
        "health": health,
        "queue": {
            "queued": queue.get("queued"),
            "running": queue.get("running"),
            "capacity": queue.get("max_global_inflight"),
            "tab_limit": queue.get("gateway_tabs_per_account"),
        },
        "limits": {
            "max_running_accounts": settings.get("max_running_accounts"),
            "global_max_inflight": settings.get("global_max_inflight"),
            "max_batch_images": settings.get("max_batch_images"),
            "idle_worker_shutdown_minutes": settings.get("idle_worker_shutdown_minutes"),
        },
    }

    if not args.json:
        print(f"panel      : {args.panel} (v{health.get('version')})")
        print(f"workers    : {health.get('running_workers')} running / {len(accounts)} account(s)")
        total_mb = sum(a.get("memory_mb") or 0 for a in accounts)
        print(f"worker RAM : {round(total_mb, 1)} MB across {len(accounts)} account(s)")
        print(f"queue      : {queue.get('queued')} queued · {queue.get('running')} running")
        print(
            "limits     : global={} · per-account tabs={} · batch={}".format(
                settings.get("global_max_inflight"),
                settings.get("gateway_tabs_per_account"),
                settings.get("max_batch_images"),
            )
        )
        print(f"disk       : {settings.get('disk_free_mb')} MB free · low={settings.get('disk_low')}")
        print(f"unresolved : {health.get('unresolved_unknown')} unknown job(s)")

    if not args.submit:
        payload = {"snapshot": snapshot, "submitted": False}
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("\nread-only mode. Add --submit --yes for a live probe (consumes provider quota).")
        return 0
    if not args.yes:
        sys.exit("Refusing to submit real jobs without --yes (this consumes provider quota).")
    if not accounts:
        sys.exit("No active accounts to submit to.")

    run = uuid.uuid4().hex[:8]
    submitted = []
    started = time.time()
    for account in accounts:
        for index in range(args.jobs_per_account):
            payload = {
                "account_id": account["id"],
                "prompt": f"capacity probe {run}: a single small blue circle centered on white",
                "aspect_ratio": "1:1",
                "quality_preference": "draft",
                "n": args.n,
                "idempotency_key": f"cap-{run}-{account['id'][:8]}-{index}",
            }
            response = client.post("/api/generate", json=payload, headers=headers)
            if response.status_code != 202:
                print(f"submit failed for {account['name']}: {response.status_code} {response.text[:120]}")
                continue
            submitted.append(response.json()["id"])
    if not submitted:
        sys.exit("No job was accepted; nothing to measure.")
    if not args.json:
        print(f"\nsubmitted {len(submitted)} job(s); waiting for settlement ...")

    deadline = time.time() + args.timeout
    jobs = {}
    while time.time() < deadline:
        jobs = {}
        for job_id in submitted:
            row = client.get(f"/api/jobs/{job_id}").json()
            jobs[job_id] = row
        if all(row["status"] in SETTLED for row in jobs.values()):
            break
        time.sleep(args.interval)
    wall = round(time.time() - started, 1)

    values = list(jobs.values())
    aggregate = summarize(values)
    analytics = client.get("/api/analytics?hours=5").json()
    health_after = client.get("/api/health").json()
    accounts_after = [a for a in client.get("/api/accounts").json() if not a.get("archived_at")]
    ram = round(sum(a.get("memory_mb") or 0 for a in accounts_after), 1)
    images_per_hour = round(aggregate["images"] / wall * 3600, 1) if wall > 0 else None

    report = {
        "snapshot": snapshot,
        "run": run,
        "submitted": len(submitted),
        "wall_sec": wall,
        "aggregate": aggregate,
        "images_per_hour_measured": images_per_hour,
        "queue_wait_p50_sec": analytics.get("queue_wait_p50_sec"),
        "queue_wait_p95_sec": analytics.get("queue_wait_p95_sec"),
        "generation_p50_sec": analytics.get("p50_sec"),
        "generation_p95_sec": analytics.get("p95_sec"),
        "unresolved_unknown": health_after.get("unresolved_unknown"),
        "worker_ram_mb": ram,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("\n=== measured ===")
    print(f"wall time          : {wall} s")
    print(f"jobs settled       : {aggregate['settled']}/{report['submitted']}")
    print(f"images stored      : {aggregate['images']}")
    print(f"status counts      : {json.dumps(aggregate['counts'], sort_keys=True)}")
    print(f"sustained images/h : {images_per_hour}")
    print(f"queue wait p50/p95 : {report['queue_wait_p50_sec']} / {report['queue_wait_p95_sec']} s")
    print(f"generation p50/p95 : {report['generation_p50_sec']} / {report['generation_p95_sec']} s")
    print(f"worker RAM         : {ram} MB")
    print(
        "\nRecord these numbers in REPORT_FA.md; do not extrapolate beyond what the "
        "provider actually allowed in this run."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
