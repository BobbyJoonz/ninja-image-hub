"""ChatGPT plan and subscription awareness.

ChatGPT's own account-check payload (fetched from inside the signed-in worker browser)
contains the authoritative plan: plan_type, plan_display_name, subscription_plan,
has_active_subscription, expires_at / renews_at / cancels_at, will_renew and whether the
account ever paid. This module normalises that into a small, cacheable summary.
"""
import json
import re
from datetime import datetime, timedelta, timezone

PLAN_MATCHERS = (
    ("pro", ("pro",)),
    ("team", ("team", "business", "enterprise")),
    ("plus", ("plus",)),
    ("free", ("guest", "free")),
)


def _parse_dt(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days_left(end):
    if end is None:
        return None
    delta = end - datetime.now(timezone.utc)
    return max(0, delta.days + (1 if delta.seconds else 0))


def _plan_name(subscription_plan, plan_type, display):
    blob = " ".join(x for x in (subscription_plan, plan_type) if x).lower()
    for name, needles in PLAN_MATCHERS:
        if any(needle in blob for needle in needles):
            return name
    return (display or "unknown").strip().lower() or "unknown"


def parse_entitlement(payload):
    """Normalise the raw `/login/plan` payload; safe with partial/absent data."""
    payload = payload or {}
    check = ((payload.get("check") or {}).get("body") or {})
    accounts = check.get("accounts") or {}
    entry = {}
    if isinstance(accounts, dict):
        candidate = accounts.get("default")
        if isinstance(candidate, dict):
            entry = candidate
        else:
            entry = next((v for v in accounts.values() if isinstance(v, dict)), {})

    entitlement = entry.get("entitlement") or {}
    account = entry.get("account") or {}
    last = entry.get("last_active_subscription") or {}

    plan_type = str(account.get("plan_type") or "").strip()
    subscription = str(entitlement.get("subscription_plan") or "").strip()
    display = str(account.get("plan_display_name") or "").strip()

    active = bool(entitlement.get("has_active_subscription"))
    expires = _parse_dt(entitlement.get("expires_at"))
    renews = _parse_dt(entitlement.get("renews_at"))
    cancels = _parse_dt(entitlement.get("cancels_at"))
    end = expires or cancels
    plan = _plan_name(subscription, plan_type, display)

    return {
        "plan": plan,
        "label": display or plan,
        "active": active,
        "is_free": plan in ("free", "guest") or not active,
        "plan_type": plan_type or None,
        "subscription_plan": subscription or None,
        "renews_at": renews.isoformat() if renews else None,
        "expires_at": expires.isoformat() if expires else None,
        "cancels_at": cancels.isoformat() if cancels else None,
        "ends_at": end.isoformat() if end else None,
        "days_left": _days_left(end),
        "will_renew": bool(last.get("will_renew")),
        "previously_paid": bool(account.get("has_previously_paid_subscription")),
        "session_expires": (payload.get("session") or {}).get("expires"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "chatgpt-account-entitlement",
    }


def summary(account):
    """Cached summary from the account row, with days_left recomputed for today."""
    raw = (account or {}).get("plan_meta")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["days_left"] = _days_left(_parse_dt(data.get("ends_at")))
    return data

PLAN_RANK = {
    "unknown": -1,
    "free": 0,
    "guest": 0,
    "plus": 1,
    "pro": 2,
    "business": 3,
    "team": 3,
    "enterprise": 4,
}

SETTINGS_PLAN_WORDS = (
    ("enterprise", "enterprise"),
    ("business", "business"),
    ("team", "team"),
    ("pro", "pro"),
    ("plus", "plus"),
    ("free", "free"),
    ("guest", "free"),
)


def parse_settings(payload):
    """Read the plan from ChatGPT's Settings -> Account page text.

    Workspace members see the workspace plan here (e.g. Business) even when the
    per-account entitlement endpoint still reports the personal Free plan.
    """
    texts = []
    workspace_id = None
    if isinstance(payload, dict):
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            if value.get("workspaceId"):
                workspace_id = value["workspaceId"]
            if value.get("text"):
                texts.append(str(value["text"]))
    joined = "\n".join(texts)
    if not joined.strip():
        return None
    match = re.search(
        r"\bPlan\b[^\w]{0,40}?(Enterprise|Business|Team|Pro|Plus|Free|Guest)\b",
        joined,
        re.IGNORECASE,
    )
    if not match:
        return None
    word = match.group(1).lower()
    plan = next((key for needle, key in SETTINGS_PLAN_WORDS if needle == word), "unknown")
    return {
        "plan": plan,
        "label": match.group(1).title(),
        "source": "chatgpt-settings-page",
        "workspace_id": workspace_id,
        "workspace_plan": plan in ("business", "team", "enterprise"),
        "checked_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }


def _rank(plan):
    return PLAN_RANK.get(str(plan or "").lower(), -1)


def needs_workspace_check(base):
    """The account API reports the personal (default) entitlement; workspaces differ."""
    return str((base or {}).get("plan") or "").lower() in ("free", "guest", "unknown")


def merge(base, detected):
    """Prefer the stronger plan; keep dates and mark the source honestly."""
    base = dict(base or {})
    detected = dict(detected or {})
    if _rank(detected.get("plan")) > _rank(base.get("plan")) or needs_workspace_check(base):
        if _rank(detected.get("plan")) >= 0:
            base.update(
                {
                    "plan": detected.get("plan"),
                    "label": detected.get("label") or detected.get("plan"),
                    "is_free": detected.get("plan") in ("free", "guest"),
                    "active": detected.get("plan") not in ("free", "guest", "unknown"),
                    "source": detected.get("source"),
                    "workspace_plan": bool(detected.get("workspace_plan")),
                    "workspace_id": detected.get("workspace_id") or base.get("workspace_id"),
                    "settings_checked_at": detected.get("checked_at"),
                }
            )
    return base


def with_workspace_id(base, workspace_id):
    if workspace_id:
        base = dict(base or {})
        base["workspace_id"] = workspace_id
    return base
