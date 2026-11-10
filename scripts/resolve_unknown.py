"""List and reconcile unresolved `unknown` jobs safely.

`unknown` means the upstream outcome was not confirmed. This tool never re-submits: it only
records the operator's verdict through POST /api/jobs/{id}/resolve.

    python scripts/resolve_unknown.py                      # read-only list
    python scripts/resolve_unknown.py --match "page did not load"
    python scripts/resolve_unknown.py --resolution not_delivered \
        --match "page did not load" --yes

Safety: read-only unless `--yes` is given; only jobs that are still `unknown` and have no
resolution are touched; the panel records an audit entry without the note. Resolving also
releases that job's reference-image workspace.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _panel import Panel, PanelError, password_from  # noqa: E402

VALID = ("delivered_manually", "not_delivered", "abandoned")


def filter_unknown(jobs, match="", older_than_hours=0, ids=None, now=None):
    """Pure selection: unresolved unknown jobs, optionally by text/age/explicit ids."""
    now = time.time() if now is None else now
    wanted = {str(i) for i in (ids or [])}
    needle = (match or "").strip().lower()
    selected = []
    for job in jobs:
        if job.get("status") != "unknown" or job.get("resolution"):
            continue
        if wanted and job.get("id") not in wanted:
            continue
        if needle:
            haystack = " ".join(
                str(job.get(key) or "") for key in ("id", "prompt", "error")
            ).lower()
            if needle not in haystack:
                continue
        if older_than_hours:
            from datetime import datetime, timezone

            created = job.get("created_at") or ""
            try:
                created_at = datetime.fromisoformat(created)
            except ValueError:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if (now - created_at.timestamp()) < older_than_hours * 3600:
                continue
        selected.append(job)
    return selected


def describe(job):
    return {
        "id": job.get("id"),
        "created_at": job.get("created_at"),
        "error": (job.get("error") or "")[:120],
        "refs": job.get("reference_count") or 0,
        "prompt": (job.get("prompt") or "")[:60],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="http://127.0.0.1:8081")
    parser.add_argument("--password", default=None)
    parser.add_argument(
        "--match",
        default="",
        help="only jobs whose id/prompt/error contains this text (case-insensitive)",
    )
    parser.add_argument(
        "--older-than-hours", type=float, default=0, help="only jobs older than this"
    )
    parser.add_argument("--ids", nargs="*", default=[], help="explicit job ids")
    parser.add_argument(
        "--resolution",
        choices=VALID,
        default=None,
        help="verdict to record (requires --yes); omit for read-only",
    )
    parser.add_argument("--note", default="", help="optional operator note (max 500 chars)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually write the resolution; without it this is a dry run",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    password = password_from(args)
    try:
        with Panel(args.panel, password) as panel:
            panel.login()
            jobs = panel.jobs(limit=200)
            selected = filter_unknown(
                jobs,
                match=args.match,
                older_than_hours=args.older_than_hours,
                ids=args.ids,
            )

            if args.json:
                print(
                    json.dumps(
                        {
                            "selected": [describe(job) for job in selected],
                            "apply": bool(args.resolution and args.yes),
                            "resolution": args.resolution,
                        },
                        indent=2,
                    )
                )
            else:
                print(f"unresolved unknown jobs: {len(selected)}")
                for job in selected:
                    info = describe(job)
                    print(
                        f"  {str(info['id'])[:8]}  {info['created_at']}  refs={info['refs']}  "
                        f"{info['error']}"
                    )
                if not selected:
                    print("Nothing to reconcile.")
                    return 0
                if not args.resolution or not args.yes:
                    print(
                        "\ndry run. Re-run with --resolution "
                        f"{{{ '|'.join(VALID) }}} --yes to record a verdict."
                    )
                    return 0

            if not args.resolution or not args.yes:
                return 0

            done = 0
            failed = 0
            for job in selected:
                try:
                    panel.resolve(job["id"], args.resolution, args.note)
                    done += 1
                    print(f"resolved {str(job['id'])[:8]} as {args.resolution}")
                except PanelError as exc:
                    failed += 1
                    print(f"FAILED {str(job['id'])[:8]}: {exc}")
            print(f"\nresolved {done}, failed {failed}. Nothing was re-submitted upstream.")
            return 1 if failed else 0
    except PanelError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
