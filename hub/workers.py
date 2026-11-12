import re
import httpx
from . import plans
import psutil

from .worker_process import (  # re-exported: one import surface for the runtime/tests
    WORKER_ENV_ALLOWLIST,
    _identity_after_start,
    _kill_tree,
    _read_auto_login,
    allocate_port,
    display_mode,
    display_state,
    ensure_worker_copy,
    focus_worker_window,
    listening,
    port_available,
    process_state,
    reclaim_orphan_worker,
    runtime_installed,
    start_worker,
    start_worker_async,
    stop_worker,
    touch_account,
    upstream,
    upstream_probe,
    wake_worker_async,
    worker_command,
    worker_env,
    worker_rss_mb,
)  # noqa: F401 - re-exported for the runtime and tests

from .worker_setup import (  # noqa: F401 - re-exported for the runtime/tests
    WORKER_ENV_ALLOWLIST,
    _read_auto_login,
    display_mode,
    display_state,
    ensure_worker_copy,
    runtime_installed,
    worker_command,
    worker_env,
)

from .gateway_patches import (  # re-exported for compatibility and tests
    FAST_INSERT_MARKER,
    PATCH_MARKERS,
    _patch_fast_prompt_insert,
    _patch_image_failure_detect,
    _patch_launch_args,
    _patch_live_view,
    _patch_login_diagnostics,
    _patch_plan_probe,
    _patch_popup_follow,
    _patch_project_url,
    _patch_settings_probe,
    apply_worker_patches,
)  # noqa: F401 - re-exported for the runtime and tests



def safe_account(ctx, acc):
    account = {
        k: acc.get(k)
        for k in [
            "id",
            "name",
            "email_label",
            "autostart",
            "created_at",
            "archived_at",
            "plan_label",
            "plan_checked_at",
            "parallel_limit",
            "cooldown_until",
            "dispatch_enabled",
            "project_url",
            "plan_meta",
            "identity_email",
            "identity_workspace",
            "identity_checked_at",
            "identity_state",
        ]
    } | ctx.account_status(acc)
    account["plan"] = plans.summary(acc)
    account["auto_login_configured"] = (
        ctx.DATA / "accounts" / acc["id"] / "auto-login.json"
    ).is_file()
    return account


















def _parse_metrics(text):
    """Session state plus the last login blocker, from the gateway's Prometheus output."""
    state = "unknown"
    match = re.search(
        "^gpt_web_gateway_session_valid\\s+([01])(?:\\.0)?\\s*$", text, re.M
    )
    if match:
        state = "connected" if match.group(1) == "1" else "unverified"
    blocker = None
    failures = re.findall(
        r'^gpt_web_gateway_login_failures_total\{blocker="([a-z_]+)"\}\s+([0-9.]+)\s*$',
        text,
        re.M,
    )
    for name, value in failures:
        try:
            if float(value) > 0:
                blocker = name
                break
        except ValueError:
            continue
    return {"state": state, "blocker": blocker if state != "connected" else None}


def session_snapshot(ctx, acc):
    """One /metrics call: session validity plus why the gateway considers it invalid."""
    try:
        r = httpx.get(trust_env=False, url=ctx.upstream(acc) + "/metrics", timeout=1.6)
        if r.status_code != 200:
            return {"state": "unknown", "blocker": None}
        return _parse_metrics(r.text)
    except httpx.RequestError:
        return {"state": "unknown", "blocker": None}


def session_probe(ctx, acc):
    return session_snapshot(ctx, acc)["state"]


def settings_plan(ctx, acc):
    """Read the plan from Settings -> Account (workspace plans live here)."""
    try:
        response = httpx.get(
            trust_env=False,
            url=ctx.upstream(acc) + "/login/settings",
            headers={"Authorization": f"Bearer {acc['api_secret']}"},
            timeout=45,
        )
    except httpx.RequestError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return plans.parse_settings(payload)


def expected_identity(ctx, acc):
    """Who should be signed in: auto-login e-mail, else a real e-mail label."""
    try:
        creds = _read_auto_login(ctx, acc["id"]) or {}
    except Exception:
        creds = {}
    email = (creds.get("email") or "").strip()
    if not email:
        label = (acc.get("email_label") or "").strip()
        if "@" in label:
            email = label
    return {"email": email}


def identity_state_for(ctx, acc, detected_email, detected_workspace):
    """Compare the browser's identity with what this account is supposed to be."""
    expected = expected_identity(ctx, acc)
    email = (detected_email or "").strip()
    if not email:
        return "unknown"
    if expected["email"] and expected["email"].lower() != email.lower():
        return "mismatch"
    previous = (acc.get("identity_workspace") or "").strip()
    if previous and detected_workspace and previous != detected_workspace:
        return "workspace_changed"
    return "verified"


def plan_snapshot(ctx, acc):
    """Read the plan from ChatGPT through the worker (sync; run off the event loop).

    The per-account entitlement endpoint reports the *personal* plan, so a member of
    a Business/Pro workspace can look "Free" there. When that happens we ask the
    Settings page, which shows the active workspace's plan.
    """
    try:
        response = httpx.get(
            trust_env=False,
            url=ctx.upstream(acc) + "/login/plan",
            headers={"Authorization": f"Bearer {acc['api_secret']}"},
            timeout=20,
        )
    except httpx.RequestError:
        return None
    if response.status_code != 200:
        return None
    try:
        raw = response.json()
    except ValueError:
        return None
    base = plans.parse_entitlement(raw)
    detected = settings_plan(ctx, acc)  # always: identity + workspace plan live here
    if detected:
        base = plans.merge(base, detected)
        base["identity"] = {
            "email": detected.get("email"),
            "workspace": detected.get("workspace_id"),
            "checked_at": detected.get("checked_at"),
        }
    return base


def plan_snapshot_for_account(ctx, account_id):
    acc = ctx.query_account(account_id)
    if not acc:
        return None
    return plan_snapshot(ctx, acc)


def worker_display(ctx, acc):
    """The X display the worker browser runs on (Linux), for the operator's VNC access.

    Returns None on Windows/macOS or when the process is not running. x11vnc against this
    display is the only way to complete providers that need a real browser (Google SSO,
    CAPTCHA) on a headless server.
    """
    with ctx.proc_guard:
        process = ctx.processes.get(acc["id"])
    if not process or process.poll() is not None:
        return None
    try:
        return psutil.Process(process.pid).environ().get("DISPLAY") or None
    except psutil.Error:
        return None


def account_status(ctx, acc):
    if acc.get("archived_at"):
        return {
            "runtime": "archived",
            "session": "unknown",
            "memory_mb": None,
            "restart_required": False,
            "display": None,
        }
    state = ctx.process_state(acc)
    if state == "stopped":
        return {
            "runtime": "stopped",
            "session": "unknown",
            "memory_mb": None,
            "restart_required": False,
            "display": None,
        }
    launch = ctx.worker_launch_settings.get(acc["id"])
    drift = bool(
        launch
        and launch != (ctx.setting("gateway_max_tabs"), ctx.setting("max_batch_images"))
    )
    if not ctx.upstream_probe(acc):
        return {
            "runtime": "starting",
            "session": "unknown",
            "memory_mb": ctx.worker_rss_mb(acc["id"]),
            "restart_required": drift,
            "display": None,
        }
    snapshot = ctx.session_snapshot(acc)
    return {
        "runtime": "online",
        "session": snapshot["state"],
        "session_blocker": snapshot.get("blocker"),
        "memory_mb": ctx.worker_rss_mb(acc["id"]),
        "restart_required": drift,
        "display": worker_display(ctx, acc),
    }
































