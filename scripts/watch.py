"""Poll the Hub and alert on the conditions that actually cost money or data.

    python scripts/watch.py --once                       # cron/systemd-timer friendly
    python scripts/watch.py --interval 60 --webhook https://example/hook
    NINJA_ADMIN_PASSWORD=... python scripts/watch.py     # also checks queue age

Conditions (each can be tuned or disabled with 0):

* panel unreachable for N consecutive polls
* free disk below the Hub's own min_free_disk_mb (or --disk-min-mb)
* unresolved ``unknown`` jobs (upstream outcome uncertain, nobody reconciled)
* queue not draining: queued work while zero workers run
* oldest queued job older than --queued-age-min (needs admin password)

Alerts are de-duplicated through a state file and re-sent after --repeat-min minutes.
The webhook receives JSON ``{"text": ..., "alerts": [...], "health": {...}}``.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required (pip install -r requirements.txt)")


def evaluate(health, queue=None, options=None):
    """Pure alert evaluation; returns a list of alert dicts."""
    options = options or {}
    alerts = []
    if health is None:
        alerts.append(
            {
                "key": "unreachable",
                "text": "Panel is unreachable; the control plane may be down.",
            }
        )
        return alerts
    disk_min = int(options.get("disk_min_mb", 0) or 0)
    free = health.get("disk_free_mb")
    if disk_min and free is not None and free < disk_min:
        alerts.append(
            {
                "key": "disk_low",
                "text": f"Low disk: {free} MB free (< {disk_min} MB). Dispatch is paused.",
            }
        )
    elif health.get("disk_low"):
        alerts.append(
            {
                "key": "disk_low",
                "text": f"Hub reports low disk ({health.get('disk_free_mb')} MB free).",
            }
        )
    unresolved = int(health.get("unresolved_unknown") or 0)
    unresolved_min = int(options.get("unresolved_min", 1) or 0)
    if unresolved_min and unresolved >= unresolved_min:
        alerts.append(
            {
                "key": "unresolved_unknown",
                "text": f"{unresolved} unknown job(s) awaiting reconciliation; check for a delivered image before re-submitting.",
            }
        )
    queued = int(health.get("queued") or 0)
    workers = int(health.get("running_workers") or 0)
    accounts = int(health.get("accounts") or 0)
    if int(options.get("workers_down_min", 2) or 0):
        # Zero workers with queued work for N consecutive polls is a stall, not idleness.
        if queued > 0 and workers == 0 and accounts > 0:
            alerts.append(
                {
                    "key": "workers_down",
                    "text": f"{queued} job(s) queued but no browser worker is running.",
                }
            )
    effort_below = int(health.get("effort_below_requested_24h") or 0)
    effort_min = int(options.get("effort_below_min", 1) or 0)
    if effort_min and effort_below >= effort_min:
        alerts.append(
            {
                "key": "effort_downgrade",
                "text": f"{effort_below} image job(s) in 24h ran below the requested thinking effort; check the Jobs badges before trusting output quality.",
            }
        )
    age_limit = float(options.get("queued_age_min", 0) or 0)
    if queue and age_limit:
        oldest = queue.get("oldest_queued_age_min")
        if oldest is not None and oldest > age_limit:
            alerts.append(
                {
                    "key": "queue_age",
                    "text": f"Oldest queued job is {oldest} min old (> {age_limit} min).",
                }
            )
    return alerts


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    try:
        Path(path).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def fetch_health(base):
    response = httpx.get(base.rstrip("/") + "/api/health", timeout=10)
    response.raise_for_status()
    return response.json()


def login(base, password):
    client = httpx.Client(base_url=base.rstrip("/"), timeout=15)
    result = client.post("/api/auth/login", json={"password": password})
    result.raise_for_status()
    csrf = result.json()["csrf"]
    return client, {"x-csrf-token": csrf}


def fetch_queue(client, headers):
    response = client.get("/api/queue", headers=headers)
    response.raise_for_status()
    return response.json()


def send_webhook(url, text, alerts, health):
    payload = {"text": text, "alerts": alerts, "health": health}
    response = httpx.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.status_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="http://127.0.0.1:8081")
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="single poll; exit 1 if alerts fired")
    parser.add_argument(
        "--webhook",
        default=os.getenv("NINJA_WEBHOOK_URL", ""),
        help="POST JSON alerts to this URL (default: NINJA_WEBHOOK_URL)",
    )
    parser.add_argument("--state", default=str(Path.home() / ".ninja-hub-watch.json"))
    parser.add_argument("--repeat-min", type=float, default=30)
    parser.add_argument("--disk-min-mb", type=int, default=0, help="override; 0 uses the Hub value")
    parser.add_argument("--unresolved-min", type=int, default=1)
    parser.add_argument("--queued-age-min", type=float, default=15)
    parser.add_argument("--effort-below-min", type=int, default=1)
    parser.add_argument("--password", default=os.getenv("NINJA_ADMIN_PASSWORD", ""))
    args = parser.parse_args()

    state = load_state(args.state)
    unreachable_streak = 0
    options = {
        "disk_min_mb": args.disk_min_mb,
        "unresolved_min": args.unresolved_min,
        "queued_age_min": args.queued_age_min,
        "workers_down_min": 2,
        "effort_below_min": args.effort_below_min,
    }
    client = headers = None
    while True:
        health = None
        queue = None
        try:
            health = fetch_health(args.panel)
            unreachable_streak = 0
            if args.password:
                if client is None:
                    client, headers = login(args.panel, args.password)
                queue = fetch_queue(client, headers)
        except Exception as exc:  # noqa: BLE001 - any failure is "unreachable"
            unreachable_streak += 1
            client = None
            if unreachable_streak >= 2:
                health = None
            print(f"[watch] poll failed ({unreachable_streak}): {exc}", file=sys.stderr)

        alerts = evaluate(health, queue, options)
        now = time.time()
        fresh = []
        for alert in alerts:
            last = state.get(alert["key"], 0)
            if now - last >= args.repeat_min * 60:
                fresh.append(alert)
                state[alert["key"]] = now
        if fresh:
            text = "Ninja Image Hub alert:\n- " + "\n- ".join(a["text"] for a in fresh)
            print(text)
            if args.webhook:
                try:
                    send_webhook(args.webhook, text, fresh, health or {})
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] webhook failed: {exc}", file=sys.stderr)
            save_state(args.state, state)
        else:
            print(f"[watch] ok ({len(alerts)} alert(s), {len(fresh)} new)")

        if args.once:
            return 1 if fresh else 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
