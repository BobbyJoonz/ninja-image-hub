"""Worker process lifecycle: ports, environment, launch, stop and orphan reclaim.

The diagnostics/status helpers stay in workers.py; this module owns the child-process
side only. Both are re-exported from workers.py so the runtime and tests keep one entry point.
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
import psutil
from fastapi import HTTPException
from sqlalchemy import select

from .gateway_patches import apply_worker_patches


def worker_rss_mb(ctx, account_id):
    with ctx.proc_guard:
        p = ctx.processes.get(account_id)
    if not p or p.poll() is not None:
        return None
    try:
        parent = psutil.Process(p.pid)
        tree = [parent] + parent.children(recursive=True)
        total = 0
        for child in tree:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return round(total / 1048576, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def upstream(ctx, acc):
    return f"http://127.0.0.1:{acc['port']}"


def runtime_installed(ctx):
    return (
        (ctx.VENDOR / "src" / "server.js").exists()
        and (ctx.VENDOR / "node_modules").is_dir()
        and (shutil.which("node") is not None)
    )


def listening(ctx, port: int):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def port_available(ctx, port):
    if ctx.listening(port):
        return False
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def allocate_port(ctx):
    """Pick a free worker port.

    GATEWAY_PORT_BASE isolates tests (and a second dev instance) from the live panel's
    41000-41999 range instead of racing it for ports.
    """
    try:
        base = int(os.getenv("GATEWAY_PORT_BASE", "41000"))
    except ValueError:
        base = 41000
    with ctx.engine.connect() as c:
        used = {r[0] for r in c.execute(select(ctx.accounts.c.port))}
    for p in range(base, base + 1000):
        if p not in used and ctx.port_available(p):
            return p
    raise HTTPException(503, "No free local worker port.")


def process_state(ctx, acc):
    with ctx.proc_guard:
        p = ctx.processes.get(acc["id"])
        if p is not None and p.poll() is None:
            return "running"
    return "stopped"


def upstream_probe(ctx, acc):
    try:
        r = httpx.get(trust_env=False, url=ctx.upstream(acc) + "/health", timeout=0.6)
        return r.status_code == 200
    except httpx.RequestError:
        return False


def ensure_worker_copy(ctx, acc):
    if not ctx.runtime_installed():
        raise RuntimeError(
            "Gateway runtime is not installed. Run python scripts/prepare_gateway.py."
        )
    worker = ctx.DATA / "accounts" / acc["id"] / "gateway"
    worker.mkdir(parents=True, exist_ok=True)
    if not (worker / "src" / "server.js").exists():
        shutil.copytree(ctx.VENDOR / "src", worker / "src", dirs_exist_ok=True)
        shutil.copytree(ctx.VENDOR / "public", worker / "public", dirs_exist_ok=True)
        for filename in ("package.json", "package-lock.json"):
            if (ctx.VENDOR / filename).exists():
                shutil.copy2(ctx.VENDOR / filename, worker / filename)
        src = worker / "src" / "server.js"
        s = src.read_text(encoding="utf-8")
        before = "app.listen(PORT, (err) =>"
        if s.count(before) != 1:
            raise RuntimeError(
                "Upstream server entry changed: refuse unsafe network binding. Review upstream and update adapter."
            )
        src.write_text(
            s.replace(before, "app.listen(PORT, '127.0.0.1', (err) =>"),
            encoding="utf-8",
        )
    (worker / "auth").mkdir(exist_ok=True)
    (worker / "public" / "images").mkdir(parents=True, exist_ok=True)
    # Apply the gateway guards on every start, so existing workspaces get them too.
    apply_worker_patches(worker)
    return worker


WORKER_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "Path",
        "PATHEXT",
        "SystemRoot",
        "SYSTEMROOT",
        "SystemDrive",
        "ComSpec",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "WINDIR",
        "USERNAME",
        "USERDOMAIN",
        "COMPUTERNAME",
        "SESSIONNAME",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "CHROME_PATH",
        "PUPPETEER_EXECUTABLE_PATH",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _read_auto_login(ctx, account_id):
    """Per-account credentials for the gateway's own re-login, if the operator stored them."""
    import json

    path = ctx.DATA / "accounts" / account_id / "auto-login.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("email") or not data.get("password"):
        return None
    return data


def worker_env(ctx, acc):
    """Environment for the third-party gateway process.

    Only an explicit allowlist of OS variables is inherited: APP_SECRET, ADMIN_PASSWORD,
    AGENT_API_KEY, DATABASE_URL and every other Hub secret must never reach a process we
    did not write, nor its browser children. The only credentials passed are the account's
    own optional auto-login values, which live in the data directory.
    """
    env = {key: value for key, value in os.environ.items() if key in WORKER_ENV_ALLOWLIST}
    auto = _read_auto_login(ctx, acc["id"])
    env.update(
        {
            "PORT": str(acc["port"]),
            "API_KEY": acc["api_secret"],
            "NODE_PATH": str(ctx.VENDOR / "node_modules"),
            "HEADLESS": "false",
            "MAX_CONCURRENT_JOBS": str(ctx.setting("gateway_max_tabs")),
            "MAX_QUEUE_SIZE": "2",
            "MAX_BATCH_N": str(ctx.setting("max_batch_images")),
            "RENDER_WAIT_SEC": str(ctx.setting("render_wait_sec")),
            "MIN_TURN_STAGGER_SEC": "10",
            "MIN_JOB_GAP_SEC": "60",
            "CHATGPT_EMAIL": (auto or {}).get("email", ""),
            "CHATGPT_PASSWORD": (auto or {}).get("password", ""),
            "CHATGPT_TOTP_SECRET": (auto or {}).get("totp_secret", ""),
            "CHATGPT_PROJECT_URL": str(acc.get("project_url") or ""),
            "DEBUG_SNAPSHOTS": "0",
            "STALL_SNAPSHOTS": "0",
        }
    )
    return env


def _kill_tree(pid, timeout=6):
    """Terminate a process and its children (Chrome is a child of the Node gateway)."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        return
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        parent.terminate()
    except psutil.Error:
        pass
    done, alive = psutil.wait_procs([parent] + children, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass


def reclaim_orphan_worker(ctx, acc):
    """Kill a gateway left behind by a hard-killed panel so the port can be rebound.

    A previous process that died without its shutdown hook (taskkill /F, OOM, container
    stop) leaves `node src/server.js` and its Chrome children running. They hold the
    account port, so the next start is refused forever. Only processes that provably
    belong to this account's workspace are touched.
    """
    if ctx.port_available(acc["port"]):
        return False
    worker_dir = str(ctx.DATA / "accounts" / acc["id"] / "gateway")
    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name.startswith("node"):
                continue
            command = " ".join(proc.info.get("cmdline") or [])
            if "src/server.js" not in command:
                continue
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = ""
            if worker_dir not in command and cwd != worker_dir:
                continue
            _kill_tree(proc.pid)
            killed += 1
        except psutil.Error:
            continue
    if not killed:
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ctx.port_available(acc["port"]):
            return True
        time.sleep(0.25)
    return ctx.port_available(acc["port"])


def touch_account(ctx, account_id):
    """Record real use of an account's browser, so the idle reaper leaves it alone."""
    ctx.account_activity[account_id] = time.monotonic()


def wake_worker_async(ctx, acc):
    """Start a worker on demand (queued job, login page) without fighting the operator.

    Deliberately separate from the crash supervisor: an operator Stop is respected unless
    the account is only idle-reaped, and repeated failed wake attempts back off so a broken
    install cannot spawn a process every two seconds.
    """
    aid = acc["id"]
    now = time.monotonic()
    state = ctx.wake_state.get(aid)
    if state and now < state["next"]:
        return False
    attempts = (state["attempts"] if state else 0) + 1
    # Back off (20s, 40s, ... up to 5 min) so a broken install cannot spawn a process every
    # scheduler tick. After many failures it keeps a slow retry instead of giving up.
    delay = min(20 * (2 ** (attempts - 1)), 300)
    ctx.wake_state[aid] = {"attempts": attempts, "next": now + delay}
    ctx.idle_stopped.discard(aid)
    ctx.suppressed_workers.discard(aid)
    ctx.worker_restart_state.pop(aid, None)
    touch_account(ctx, aid)
    ctx.start_worker_async(acc, manual=False)
    return True


def display_mode(platform_name=None, which=None, env=None):
    """How a headed browser gets a screen: desktop, X11, per-worker Xvfb, or nothing.

    The gateway deliberately runs non-headless (headless is an anti-bot signal), so a
    Linux server needs a virtual display. The Hub gives every worker its own Xvfb so one
    account cannot see another account's windows.
    """
    which = which or shutil.which
    env = env if env is not None else os.environ
    platform_name = platform_name if platform_name is not None else sys.platform
    if not platform_name.startswith("linux"):
        return "desktop"
    if env.get("DISPLAY"):
        return "x11"
    if which("xvfb-run"):
        return "xvfb"
    return "missing"


def display_state(ctx):
    """ctx-bound wrapper for the dashboard (how a headed browser gets a screen)."""
    return display_mode()


def worker_command(platform_name=None, which=None, env=None):
    """Command that launches the gateway. Xvfb is wrapped per worker, never per host."""
    cmd = ["node", "src/server.js"]
    if display_mode(platform_name, which, env) == "xvfb":
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1440x900x24"] + cmd
    return cmd


def start_worker(ctx, acc, manual=True):
    with ctx.proc_guard:
        latest = ctx.query_account(acc["id"])
        if not latest or latest["archived_at"]:
            raise RuntimeError("Account archived or removed; worker start cancelled.")
        if manual:
            # An explicit Start clears the operator stop and re-arms crash supervision.
            ctx.suppressed_workers.discard(acc["id"])
            ctx.worker_restart_state.pop(acc["id"], None)
        elif acc["id"] in ctx.suppressed_workers:
            # A stop landed while this automatic start was queued; never override it.
            return
        existing = ctx.processes.get(acc["id"])
        if existing and existing.poll() is None:
            return
        if not ctx.port_available(acc["port"]):
            # A hard-killed panel leaves its gateway behind holding this port. Reclaim only
            # a process that provably belongs to this account, then retry once.
            if not ctx.reclaim_orphan_worker(acc):
                raise RuntimeError(
                    "Worker port is occupied by a process that does not belong to this "
                    f"account (port {acc['port']}); free it and start again."
                )
        running = sum((1 for worker in ctx.processes.values() if worker.poll() is None))
        cap = ctx.setting("max_running_accounts")
        if running >= cap:
            raise RuntimeError(
                f"Safety limit: at most {cap} browser workers may run. Stop another worker or increase MAX_RUNNING_ACCOUNTS after checking available RAM."
            )
        # RAM guard: with 10 accounts a single OOM kill takes down every worker and the
        # dashboard with it. Refuse the start instead of letting the kernel decide.
        guard_mb = int(ctx.setting("min_free_memory_mb") or 0)
        if guard_mb:
            available_mb = psutil.virtual_memory().available // (1024 * 1024)
            if available_mb < guard_mb:
                raise RuntimeError(
                    f"Not enough free RAM to start another browser: {available_mb} MB "
                    f"available, min_free_memory_mb={guard_mb}. Stop an idle account, lower "
                    "max_running_accounts, or lower idle_worker_shutdown_minutes."
                )
        if display_mode() == "missing":
            raise RuntimeError(
                "Linux browser display unavailable: install xvfb (apt install xvfb) so "
                "each worker gets its own virtual display, or run under a real DISPLAY."
            )
        worker = ctx.ensure_worker_copy(acc)
        env = worker_env(ctx, acc)
        cmd = worker_command()
        log_path = ctx.DATA / "accounts" / acc["id"] / "worker.log"
        h = open(log_path, "ab", buffering=0)
        try:
            p = subprocess.Popen(
                cmd,
                cwd=str(worker),
                env=env,
                stdout=h,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
                start_new_session=os.name != "nt",
            )
        except Exception:
            h.close()
            raise
        ctx.processes[acc["id"]] = p
        ctx.log_handles[acc["id"]] = h
        ctx.worker_launch_settings[acc["id"]] = (
            ctx.setting("gateway_max_tabs"),
            ctx.setting("max_batch_images"),
        )
        ctx.idle_stopped.discard(acc["id"])
        ctx.wake_state.pop(acc["id"], None)
        touch_account(ctx, acc["id"])


def stop_worker(ctx, account_id):
    with ctx.proc_guard:
        # Remember the intentional stop so the crash supervisor does not immediately
        # bring this worker back.
        ctx.suppressed_workers.add(account_id)
        p = ctx.processes.pop(account_id, None)
        ctx.worker_restart_state.pop(account_id, None)
        ctx.worker_launch_settings.pop(account_id, None)
        h = ctx.log_handles.pop(account_id, None)
        if p and p.poll() is None:
            if os.name != "nt":
                import signal

                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                # Terminating Node alone orphans its Chrome children and leaves them
                # holding RAM/display. Kill the whole tree.
                try:
                    children = psutil.Process(p.pid).children(recursive=True)
                except psutil.Error:
                    children = []
                for child in children:
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                try:
                    p.terminate()
                except OSError:
                    pass
            try:
                p.wait(timeout=6)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal

                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    p.kill()
                p.wait(timeout=3)
            if os.name == "nt":
                psutil.wait_procs(children, timeout=3)
        if h:
            h.close()


def focus_worker_window(ctx, acc):
    """Bring the worker's Chrome window to the foreground (Windows only).

    A minimized/occluded window makes captureScreenshot slow and CDP screencast
    return black frames. Linux/Xvfb has no such state, so this is a no-op there.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    import ctypes.wintypes as wt

    with ctx.proc_guard:
        process = ctx.processes.get(acc["id"])
    if not process or process.poll() is not None:
        return False
    try:
        pids = {process.pid}
        pids.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
    except psutil.Error:
        return False
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return False
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    restored = False
    for hwnd in found:
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            restored = True
        except Exception:
            continue
    return restored


def _identity_after_start(ctx, acc):
    """Give the worker a moment, then read who actually signed in."""
    try:
        for _ in range(120):
            if ctx.process_state(acc) != "running":
                return
            if ctx.upstream_probe(acc):
                break
            time.sleep(0.5)
        plan = ctx.plan_snapshot(acc)
        if plan:
            ctx.save_detected_plan(acc["id"], plan)
    except Exception:
        pass


def start_worker_async(ctx, acc, manual=False):

    def start():
        try:
            ctx.start_worker(acc, manual=manual)
            threading.Thread(
                target=_identity_after_start, args=(ctx, acc), daemon=True
            ).start()
        except Exception as e:
            (ctx.DATA / "accounts" / acc["id"]).mkdir(parents=True, exist_ok=True)
            with (ctx.DATA / "accounts" / acc["id"] / "worker.log").open(
                "a", encoding="utf8"
            ) as f:
                f.write(f"\n[manager] startup failed: {e}\n")

    threading.Thread(target=start, daemon=True, name="worker-start").start()

