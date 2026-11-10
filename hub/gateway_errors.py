"""Gateway error classification, independent of scheduling.

`unknown` used to swallow every 5xx. These helpers turn the gateway's structured body into
either a proven failure (nothing usable was delivered) or a genuine unknown.
"""

CERTAIN_GATEWAY_FAILURE_KINDS = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "page_load_failed",
        "login_failed",
        "login_required",
        "upload_failed",
        "queue_full",
        "policy_violation",
        "refused",
        "image_generation_failed",
        "generation_failed",
    }
)


# empty kind) stays `unknown` so it is never re-submitted automatically.
CERTAIN_GATEWAY_FAILURE_KINDS = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "page_load_failed",
        "login_failed",
        "login_required",
        "upload_failed",
        "queue_full",
        "policy_violation",
        "refused",
        "image_generation_failed",
        "generation_failed",
    }
)


def parse_gateway_failure(status, data):
    """Extract error_kind/message/login_blocker from a gateway error body.

    The gateway returns these at the top level; older builds only had `error.message`, so
    a couple of narrow message fallbacks keep the classification useful after upgrades.
    Deliberately conservative: an unknown kind must stay unknown.
    """
    body = data if isinstance(data, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    kind = str(body.get("error_kind") or "").strip().lower()
    message = str(error.get("message") or "").strip()
    blocker = str(body.get("login_blocker") or "").strip()
    if not kind and message:
        lowered = message.lower()
        if "page did not load" in lowered or "content failed to load" in lowered:
            kind = "page_load_failed"
        elif "not logged in" in lowered or "login required" in lowered:
            kind = "login_failed"
        elif "upload failed" in lowered or "could not attach" in lowered:
            kind = "upload_failed"
    return {
        "status": int(status),
        "kind": kind,
        "message": message[:300],
        "blocker": blocker[:120],
        "certain_failure": kind in CERTAIN_GATEWAY_FAILURE_KINDS,
    }


def gateway_failure_text(failure):
    label = failure["kind"] or "gateway_error"
    detail = f"Gateway {label} (HTTP {failure['status']})"
    if failure["message"]:
        detail += f": {failure['message']}"
    if failure["blocker"]:
        detail += f" [login blocker: {failure['blocker']}]"
    if failure["certain_failure"]:
        detail += ". Nothing was delivered to the gallery; safe to re-submit this prompt."
    else:
        detail += ". Outcome uncertain; check the account manually before retry."
    return detail[:350]
