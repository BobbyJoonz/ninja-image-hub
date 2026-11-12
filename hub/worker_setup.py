"""Worker workspace setup: vendor copy, environment allowlist, launch command and display.

Kept separate from worker_process.py so the launch environment can be reviewed (and
unit-tested) without reading the process/supervision logic.
"""

import os
import shutil
import sys

from .gateway_patches import apply_worker_patches


def runtime_installed(ctx):
    return (
        (ctx.VENDOR / "src" / "server.js").exists()
        and (ctx.VENDOR / "node_modules").is_dir()
        and (shutil.which("node") is not None)
    )


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

