"""Seed a fresh panel for the browser e2e: fake vendor gateway, one real job, one unknown.

Never talks to ChatGPT and never consumes provider quota. Writes the JSON summary so CI
artifacts prove exactly what the browser test saw.

    python scripts/seed_demo.py --panel http://127.0.0.1:8095 \
        --password "$ADMIN_PASSWORD" --vendor /tmp/fake-vendor --out /tmp/seed.json
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _panel import Panel, PanelError, password_from  # noqa: E402
from fake_gateway import write_fake_gateway  # noqa: E402

OK_RESPONSE = {
    "status": 200,
    "applied": {
        "thinking_mode": "extended",
        "thinking_verified": True,
        "requested_verified": True,
        "thinking_fallback": None,
        "thinking_mode_mixed": False,
    },
}
UNKNOWN_RESPONSE = {"status": 500, "body": {"error": {"message": "boom"}}}


def wait_job(panel, job_id, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = panel.get_json(f"/api/jobs/{job_id}")
        if last.get("status") in ("completed", "partial", "failed", "unknown", "rate_limited"):
            return last
        time.sleep(0.15)
    raise RuntimeError(f"job {job_id[:8]} did not settle: {last}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="http://127.0.0.1:8095")
    parser.add_argument("--password", default=None)
    parser.add_argument("--vendor", required=True, help="fake gateway workspace directory")
    parser.add_argument("--out", default="", help="JSON summary output")
    args = parser.parse_args(argv)

    password = password_from(args)
    vendor, capture = write_fake_gateway(
        Path(args.vendor).parent,
        name=Path(args.vendor).name,
        responses=[OK_RESPONSE, UNKNOWN_RESPONSE],
        color="green",
    )
    summary = {"vendor": str(vendor), "capture": str(capture)}
    try:
        with Panel(args.panel, password) as panel:
            panel.login()
            accounts = [a for a in panel.get_json("/api/accounts") if not a.get("archived_at")]
            if accounts:
                account = accounts[0]
            else:
                account = panel.post_json(
                    "/api/accounts", {"name": "Browser Demo", "autostart": False}
                )
            # Restart the worker so a re-seed starts with a fresh fake-gateway response
            # sequence and never reuses stale per-process call counters.
            try:
                panel.request("POST", f"/api/accounts/{account['id']}/stop")
            except PanelError:
                pass
            time.sleep(0.5)
            panel.request("POST", f"/api/accounts/{account['id']}/start")
            deadline = time.monotonic() + 90
            state = None
            while time.monotonic() < deadline:
                state = [a for a in panel.get_json("/api/accounts") if a["id"] == account["id"]][0]
                if state["runtime"] == "online" and state["session"] == "connected":
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(f"worker did not become online: {state}")
            summary["account"] = {"id": account["id"], "name": account["name"]}

            ok = panel.post_json(
                "/api/generate",
                {
                    "account_id": account["id"],
                    "prompt": "browser e2e: a plain green square",
                    "aspect_ratio": "1:1",
                    "idempotency_key": "browser-e2e-ok-" + uuid.uuid4().hex,
                },
            )
            ok_job = wait_job(panel, ok["id"])
            if ok_job["status"] not in ("completed", "partial"):
                raise RuntimeError(f"seed success job ended {ok_job['status']}: {ok_job.get('error')}")
            summary["ok_job"] = {"id": ok_job["id"], "images": ok_job.get("image_count")}

            unknown = panel.post_json(
                "/api/generate",
                {
                    "account_id": account["id"],
                    "prompt": "browser e2e: deliberately unknown outcome",
                    "aspect_ratio": "1:1",
                    "idempotency_key": "browser-e2e-unknown-" + uuid.uuid4().hex,
                },
            )
            unknown_job = wait_job(panel, unknown["id"])
            if unknown_job["status"] != "unknown":
                raise RuntimeError(
                    f"seed unknown job ended {unknown_job['status']}: {unknown_job.get('error')}"
                )
            summary["unknown_job"] = {"id": unknown_job["id"], "status": "unknown"}
    except (PanelError, RuntimeError) as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
