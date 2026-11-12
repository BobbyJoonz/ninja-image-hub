"""Long-running soak monitor for a live panel.

Polls health/session/queue every interval and writes one CSV row per cycle. Optionally
submits a small number of real jobs so auto-login/session behaviour is exercised without
burning quota. Never fabricates results: a cycle that cannot reach the panel is recorded as
unreachable.

    python scripts/soak.py --password "$ADMIN_PASSWORD" --hours 48
    python scripts/soak.py --password ... --cycles 2 --interval 1   # smoke the monitor
    python scripts/soak.py --password ... --hours 48 --submit --yes --jobs 3

The script is deliberately quota-safe: --submit requires --yes and --jobs, and no more than
that many jobs are ever submitted.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _panel import Panel, PanelError, password_from  # noqa: E402

FIELDS = (
    "ts_utc",
    "reachable",
    "version",
    "accounts",
    "workers",
    "sessions_connected",
    "sessions_unverified",
    "sessions_out",
    "queued",
    "running",
    "unknown",
    "disk_free_mb",
    "disk_low",
    "login_issue",
    "effort_below_24h",
    "effort_mixed_24h",
    "note",
)


def build_row(health, accounts, queue, ts=None):
    """Pure row builder so the monitor's arithmetic is testable without a panel."""
    ts = ts or datetime.now(timezone.utc).isoformat()
    accounts = accounts or []
    sessions = [str(a.get("session") or "unknown") for a in accounts if not a.get("archived_at")]
    online_sessions = [
        str(a.get("session") or "unknown")
        for a in accounts
        if not a.get("archived_at") and a.get("runtime") in ("online", "running")
    ]
    connected = sum(1 for s in sessions if s == "connected")
    unverified = sum(1 for s in sessions if s == "unverified")
    out = sum(1 for s in sessions if s == "out")
    return {
        "ts_utc": ts,
        "reachable": True,
        "version": str((health or {}).get("version") or ""),
        "accounts": len(sessions),
        "workers": (health or {}).get("running_workers", 0),
        "sessions_connected": connected,
        "sessions_unverified": unverified,
        "sessions_out": out,
        "queued": (queue or {}).get("queued", 0),
        "running": (queue or {}).get("running", 0),
        "unknown": (health or {}).get("unresolved_unknown", 0),
        "disk_free_mb": (health or {}).get("disk_free_mb", ""),
        "disk_low": bool((health or {}).get("disk_low")),
        # A stopped worker is not a login failure; only an online worker that is not
        # connected to ChatGPT is worth alerting on during a soak.
        "login_issue": any(s != "connected" for s in online_sessions),
        "effort_below_24h": (health or {}).get("effort_below_requested_24h", 0),
        "effort_mixed_24h": (health or {}).get("effort_mixed_24h", 0),
        "note": "",
    }


def unreachable_row(error, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    row = {field: "" for field in FIELDS}
    row.update({"ts_utc": ts, "reachable": False, "note": str(error)[:180]})
    return row


def summarize(rows):
    rows = list(rows or [])
    if not rows:
        return {"cycles": 0, "reachable": 0, "availability": 0.0, "login_issues": 0}
    reachable = sum(1 for row in rows if row.get("reachable") in (True, "True", "true", 1))
    issues = sum(1 for row in rows if str(row.get("login_issue")).lower() in ("true", "1"))
    return {
        "cycles": len(rows),
        "reachable": reachable,
        "availability_percent": round(100.0 * reachable / len(rows), 2),
        "login_issue_cycles": issues,
        "first_cycle": rows[0].get("ts_utc"),
        "last_cycle": rows[-1].get("ts_utc"),
        "max_unknown": max(int(row.get("unknown") or 0) for row in rows),
        "max_disk_low": any(str(row.get("disk_low")).lower() in ("true", "1") for row in rows),
        "max_effort_below": max(int(row.get("effort_below_24h") or 0) for row in rows),
    }


def write_rows(path, rows, append=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not append:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in FIELDS})
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _submit_probe(panel, accounts, prompt, n=1):
    """Submit one tiny real job to the first connected/started account. Quota-aware."""
    target = next(
        (
            account
            for account in accounts
            if not account.get("archived_at")
            and account.get("runtime") in ("online", "running")
            and account.get("session") == "connected"
        ),
        None,
    )
    if target is None:
        return None
    return panel.post_json(
        "/api/generate",
        {
            "account_id": target["id"],
            "prompt": prompt,
            "aspect_ratio": "1:1",
            "n": n,
            "idempotency_key": "soak-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="http://127.0.0.1:8081")
    parser.add_argument("--password", default=None)
    parser.add_argument("--hours", type=float, default=48, help="target duration (default 48)")
    parser.add_argument(
        "--cycles", type=int, default=0, help="stop after N cycles instead of --hours (testing)"
    )
    parser.add_argument("--interval", type=float, default=900, help="seconds between cycles")
    parser.add_argument("--out", default="logs/soak.csv", help="CSV output path")
    parser.add_argument("--summary", default="", help="optional JSON summary path")
    parser.add_argument("--submit", action="store_true", help="submit real probe jobs")
    parser.add_argument("--yes", action="store_true", help="confirm quota consumption")
    parser.add_argument(
        "--jobs", type=int, default=0, help="maximum number of probe jobs to submit in total"
    )
    parser.add_argument("--prompt", default="soak probe: a plain red square")
    args = parser.parse_args(argv)

    if args.submit and (not args.yes or args.jobs <= 0):
        sys.exit("--submit requires --yes and a positive --jobs budget (real quota).")
    if args.cycles <= 0 and args.hours <= 0:
        sys.exit("Either --hours or --cycles must be positive.")

    password = password_from(args)
    out = Path(args.out)
    deadline = time.monotonic() + (args.cycles * args.interval if args.cycles else args.hours * 3600)
    rows = []
    submitted = 0
    errors = 0
    first_cycle = True
    with Panel(args.panel, password) as panel:
        panel.login()
        while True:
            ts = datetime.now(timezone.utc).isoformat()
            try:
                health = panel.health()
                accounts = panel.get_json("/api/accounts")
                queue = panel.queue()
                row = build_row(health, accounts, queue, ts)
                errors = 0
                if args.submit and submitted < args.jobs:
                    try:
                        job = _submit_probe(panel, accounts, args.prompt)
                        if job:
                            submitted += 1
                            row["note"] = f"submitted {str(job.get('id'))[:8]}"
                    except PanelError as exc:
                        row["note"] = f"submit failed: {str(exc)[:120]}"
            except Exception as exc:  # noqa: BLE001 - a soak must survive transient errors
                errors += 1
                row = unreachable_row(exc, ts)
            rows.append(row)
            write_rows(out, [row], append=not first_cycle)
            first_cycle = False
            print(
                f"[soak] {ts} reachable={row['reachable']} workers={row['workers']} "
                f"connected={row['sessions_connected']}/{row['accounts']} "
                f"queued={row['queued']} unknown={row['unknown']}",
                flush=True,
            )
            if args.cycles:
                if len(rows) >= args.cycles:
                    break
            elif time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, args.interval))

    summary = summarize(rows)
    summary["submitted_jobs"] = submitted
    summary["csv"] = str(out)
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
