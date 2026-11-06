"""Operational housekeeping: derived-file retention, log rotation and disk headroom.

Hard rules (see AGENTS.md):

* Originals (``data/gallery``), account sessions (``gateway/auth``), the database and the
  control audit are **never** pruned. Only derived, regenerable data is: WebP previews,
  temporary export ZIPs, interrupted ``.incoming-*`` spool files and worker logs.
* Runs at most once per hour from the scheduler thread, and records counts in the audit
  log when it actually removes something (no paths, prompts or credentials).
* A low disk sets ``ctx.disk_low``; the dispatcher then stops taking new work so a full
  disk fails nothing silently. Queued jobs are kept.
"""

import shutil
import sys
import time

MAINTENANCE_INTERVAL_SEC = 3600
INCOMING_MAX_AGE_SEC = 3600
LOG_BACKUP_SUFFIX = ".1"


def _older_than(path, cutoff):
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _prune_dir(directory, cutoff, prefix="", suffix=""):
    """Delete files under one directory, matching an optional name prefix/suffix."""
    removed = 0
    if not directory.is_dir():
        return 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name
        if prefix and not name.startswith(prefix):
            continue
        if suffix and not name.endswith(suffix):
            continue
        if not _older_than(entry, cutoff):
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _rotate_worker_logs(ctx, max_mb):
    """Rotate oversized worker logs. Only stopped workers: an open append handle cannot be
    renamed on Windows, and a running worker keeps writing."""
    if max_mb <= 0:
        return 0
    limit = max_mb * 1024 * 1024
    accounts_dir = ctx.DATA / "accounts"
    if not accounts_dir.is_dir():
        return 0
    with ctx.proc_guard:
        alive = {
            aid for aid, proc in ctx.processes.items() if proc.poll() is None
        }
    rotated = 0
    for log in accounts_dir.glob("*/worker.log"):
        account_id = log.parent.name
        if account_id in alive:
            continue
        try:
            if log.stat().st_size <= limit:
                continue
        except OSError:
            continue
        backup = log.with_name(log.name + LOG_BACKUP_SUFFIX)
        try:
            backup.unlink(missing_ok=True)
            log.rename(backup)
            rotated += 1
        except OSError:
            continue
    return rotated


def run_maintenance(ctx):
    """Throttled housekeeping tick. Returns the last result dictionary."""
    now = time.monotonic()
    if ctx.last_maintenance and now - ctx.last_maintenance < MAINTENANCE_INTERVAL_SEC:
        return ctx.maintenance_result
    ctx.last_maintenance = now

    days = int(ctx.setting("preview_retention_days") or 0)
    hours = int(ctx.setting("export_retention_hours") or 0)
    log_mb = int(ctx.setting("worker_log_max_mb") or 0)
    epoch = time.time()

    previews = (
        _prune_dir(ctx.DATA / "previews", epoch - days * 86400, suffix=".webp")
        if days > 0
        else 0
    )
    exports = (
        _prune_dir(ctx.DATA / "exports", epoch - hours * 3600, suffix=".zip")
        if hours > 0
        else 0
    )
    # Interrupted spool files never became images; safe to drop after an hour.
    incoming = _prune_dir(
        ctx.DATA / "gallery", epoch - INCOMING_MAX_AGE_SEC, prefix=".incoming-"
    )
    rotated = _rotate_worker_logs(ctx, log_mb)

    free_mb = None
    try:
        free_mb = int(shutil.disk_usage(str(ctx.DATA)).free // (1024 * 1024))
    except OSError:
        free_mb = None
    threshold = int(ctx.setting("min_free_disk_mb") or 0)
    disk_low = bool(threshold and free_mb is not None and free_mb < threshold)

    result = {
        "previews_removed": previews,
        "exports_removed": exports,
        "incoming_removed": incoming,
        "logs_rotated": rotated,
        "disk_free_mb": free_mb,
        "disk_low": disk_low,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    ctx.disk_free_mb = free_mb
    ctx.disk_low = disk_low
    ctx.maintenance_result = result

    if previews or exports or incoming or rotated:
        ctx.record_audit(
            "maintenance.cleanup",
            {
                "previews_removed": previews,
                "exports_removed": exports,
                "incoming_removed": incoming,
                "logs_rotated": rotated,
            },
        )
    if disk_low:
        print(
            f"[maintenance] low disk: {free_mb} MB free < min_free_disk_mb={threshold}; "
            "new dispatch is paused until space is freed",
            file=sys.stderr,
        )
    return result
