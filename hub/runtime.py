import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv
from types import SimpleNamespace
from functools import partial
from .control import cfg_int, initialize_controls
from .database import initialize_database


def create_runtime():
    ctx = SimpleNamespace()
    ctx.cfg_int = partial(cfg_int, ctx)
    ctx.ROOT = Path(__file__).resolve().parent.parent

    load_dotenv(ctx.ROOT / ".env", override=False)

    ctx.DATA = Path(os.getenv("DATA_DIR", str(ctx.ROOT / "data"))).resolve()

    ctx.DATA.mkdir(parents=True, exist_ok=True)

    (ctx.DATA / "accounts").mkdir(exist_ok=True)

    (ctx.DATA / "gallery").mkdir(exist_ok=True)

    ctx.VENDOR = ctx.ROOT / "vendor" / "gpt-web-gateway"

    initialize_database(ctx)

    ctx.SESSION_SECRET = os.getenv("APP_SECRET", "")

    ctx.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

    ctx.AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")

    if (
        not ctx.SESSION_SECRET
        or len(ctx.SESSION_SECRET) < 32
        or (not ctx.ADMIN_PASSWORD)
        or (len(ctx.ADMIN_PASSWORD) < 12)
    ):
        raise RuntimeError(
            "Missing strong APP_SECRET or ADMIN_PASSWORD. Run python scripts/bootstrap.py and restart."
        )

    initialize_controls(ctx)

    ctx.GLOBAL_MAX_INFLIGHT = 16

    # The executor must be at least as large as the configurable global cap (32), otherwise
    # jobs handed to it would queue invisibly behind the dispatcher's accounting.
    ctx.pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="image-job")

    ctx.status_pool = ThreadPoolExecutor(
        max_workers=12, thread_name_prefix="account-health"
    )
    ctx.health_pool = ThreadPoolExecutor(
        max_workers=16, thread_name_prefix="health-probe"
    )

    ctx.queue_guard = threading.RLock()

    ctx.queue_wakeup = threading.Event()

    ctx.queue_stop = threading.Event()

    ctx.dispatcher_thread = None

    ctx.active_per_account: dict[str, int] = defaultdict(int)

    ctx.active_jobs: set[str] = set()

    ctx.last_served_account = None

    # Snapshot of worker reachability, refreshed outside the queue lock.
    ctx.health_cache: dict[str, bool] = {}

    # Accounts the operator stopped explicitly; the crash supervisor must not revive them.
    ctx.suppressed_workers: set[str] = set()

    # Per-account restart backoff state for the crash supervisor.
    ctx.worker_restart_state: dict[str, dict] = {}

    # On-demand workers: last real use (monotonic), reaped by idle_worker_shutdown_minutes,
    # and the accounts the reaper stopped (wakeable again on the next job / login page).
    ctx.account_activity: dict[str, float] = {}
    ctx.idle_stopped: set[str] = set()
    ctx.wake_state: dict[str, float] = {}

    # Operational housekeeping: last run (monotonic), last result and disk headroom.
    ctx.last_maintenance = 0.0
    ctx.maintenance_result: dict = {}
    ctx.disk_free_mb = None
    ctx.disk_low = False

    ctx.processes: dict[str, subprocess.Popen] = {}

    ctx.worker_launch_settings: dict[str, tuple[int, int]] = {}

    ctx.log_handles: dict[str, object] = {}

    ctx.proc_guard = threading.RLock()

    ctx.account_mutation_locks: dict[str, threading.RLock] = {}

    ctx.login_failures: dict[str, list[float]] = {}
    return ctx
