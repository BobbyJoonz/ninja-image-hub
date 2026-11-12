"""ChatGPT reasoning-effort mapping and verification (image generation tier).

The web UI exposes a "Thinking effort" control. The gateway (pinned v2.16.11) accepts
``thinking_mode`` on ``/v1/images/generations`` and echoes what the UI actually selected:

    applied.thinking_mode       gateway mode: instant | standard | extended | extra_high | pro
    applied.thinking_verified   every image in the batch confirmed the selection
    applied.thinking_fallback   the tier it fell back to, if the requested one was not usable
    applied.requested_verified  did the applied mode equal what the caller asked for
    applied.thinking_mode_mixed turns within one batch ran on different tiers

We always request High (``extended``) by default, but the provider can refuse or downgrade
(plan/tier quota). This module only *reports* that: it never retries, never rewrites a
delivered image and never turns a surviving generation into a failure.

Worst cases covered by design:

* older gateway without the field -> no echo -> ``verified=None`` (unknown, flagged)
* plan does not offer High -> gateway reports fallback -> ``degraded=True`` (flagged)
* batch ran partially on another tier -> ``mixed=True`` (flagged)
* setting holds an unknown word -> caller falls back to the safe default (High)
"""

from collections.abc import Mapping
from typing import TypedDict

# Canonical operator-facing effort -> gateway thinking_mode.
EFFORT_TO_GATEWAY: dict[str, str] = {
    "high": "extended",
    "extra_high": "extra_high",
    "medium": "standard",
    "instant": "instant",
    "pro": "pro",
}

# Gateway modes echoed back -> canonical effort.
MODE_TO_EFFORT: dict[str, str] = {
    "extended": "high",
    "high": "high",
    "standard": "medium",
    "medium": "medium",
    "extra_high": "extra_high",
    "veryhigh": "extra_high",
    "instant": "instant",
    "pro": "pro",
}

EFFORT_LABELS: dict[str, str] = {
    "high": "High",
    "extra_high": "Extra High",
    "medium": "Medium",
    "instant": "Instant",
    "pro": "Pro",
}

VALID_EFFORTS: tuple[str, ...] = tuple(EFFORT_TO_GATEWAY)
DEFAULT_EFFORT: str = "high"
# Weakest -> strongest. Used only to decide whether the applied tier was a downgrade.
_ORDER: dict[str, int] = {
    name: index
    for index, name in enumerate(["instant", "medium", "high", "extra_high", "pro"])
}


class EffortSummary(TypedDict):
    requested: str
    applied: str | None
    applied_raw: object
    verified: bool | None
    fallback: object
    mixed: bool
    degraded: bool | None


def normalize_effort(value: object, default: str = DEFAULT_EFFORT) -> str:
    """Map free-form input (env, API) to a canonical effort, else the safe default."""
    text = str(value if value is not None else "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "extended": "high",
        "standard": "medium",
        "veryhigh": "extra_high",
        "extrahigh": "extra_high",
        "highest": "extra_high",
        "max": "extra_high",
    }
    text = aliases.get(text, text)
    return text if text in EFFORT_TO_GATEWAY else default


def gateway_mode(effort: object) -> str | None:
    return EFFORT_TO_GATEWAY.get(normalize_effort(effort))


def canonical_mode(mode: object) -> str | None:
    return MODE_TO_EFFORT.get(str(mode or "").strip().lower())


def effort_label(effort: object) -> str:
    return EFFORT_LABELS.get(normalize_effort(effort), EFFORT_LABELS[DEFAULT_EFFORT])


def summarize(applied: object, requested_effort: object) -> EffortSummary:
    """Turn the gateway echo into a record we can store and show.

    Returns canonical effort names, a tri-state ``verified`` and flags. Missing echo is
    reported as unknown (None), never as success.
    """
    requested = normalize_effort(requested_effort)
    echo: Mapping[str, object] = applied if isinstance(applied, Mapping) else {}
    applied_raw = echo.get("thinking_mode")
    applied_effort = canonical_mode(applied_raw)
    verified_raw = echo.get("thinking_verified")
    verified = None if verified_raw is None else bool(verified_raw)
    fallback_raw = echo.get("thinking_fallback")
    mixed = bool(echo.get("thinking_mode_mixed"))
    requested_verified = echo.get("requested_verified")
    if requested_verified is not None:
        # Prefer the gateway's own requested-vs-applied verdict when present.
        verified = bool(verified) if verified is not None else None
    degraded = None
    if applied_effort is not None:
        degraded = _ORDER.get(applied_effort, 0) < _ORDER.get(requested, 0)
    return {
        "requested": requested,
        "applied": applied_effort,
        "applied_raw": applied_raw,
        "verified": verified,
        "fallback": fallback_raw,
        "mixed": mixed,
        "degraded": degraded,
    }


def badge(summary: Mapping[str, object] | None) -> str | None:
    """Short operator-facing status for a job row."""
    if not summary or not summary.get("requested"):
        return None
    requested = summary["requested"]
    if isinstance(requested, str):
        want = EFFORT_LABELS.get(requested, requested)
    else:
        want = str(requested)
    applied = summary.get("applied")
    got = (
        EFFORT_LABELS.get(applied, str(summary.get("applied_raw") or "?"))
        if isinstance(applied, str)
        else str(summary.get("applied_raw") or "?")
    )
    if summary.get("mixed"):
        return f"{want} · mixed"
    if summary.get("degraded"):
        return f"{want} → {got}"
    if summary.get("verified") is True:
        return f"{want} ✓"
    if summary.get("verified") is False:
        return f"{want} ? ({got})"
    return f"{want} ?"
