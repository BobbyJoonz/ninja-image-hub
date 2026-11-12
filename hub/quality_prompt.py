"""Quality Loop scoring prompt: one master template with the user's request inside.

The default lives in code (`quality_gate.DEFAULT_REVIEW_PROMPT`). Operators can override
it at runtime, so the rubric can be tuned without shipping code. The override is a single
file (``data/quality_review_prompt.txt``, mode 0600) and must contain ``{request}``.
"""
import hashlib
import os
import pathlib
import threading

from fastapi import HTTPException

from . import quality_gate as quality
from .schemas import QualityPromptBody

MAX_TEMPLATE_BYTES = 12_000
_LOCK = threading.RLock()


def _path(ctx):
    return pathlib.Path(ctx.DATA) / "quality_review_prompt.txt"


def read_override(ctx):
    path = _path(ctx)
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        quality.validate_template(text)
    except ValueError:
        return None
    return text


def current_template(ctx):
    return read_override(ctx) or quality.DEFAULT_REVIEW_PROMPT


def prompt_meta(ctx, template):
    return {
        "source": "custom" if template != quality.DEFAULT_REVIEW_PROMPT else "default",
        "hash": hashlib.sha256(template.encode("utf-8")).hexdigest()[:12],
    }


def template_info(ctx):
    template = current_template(ctx)
    return {
        "template": template,
        "default": quality.DEFAULT_REVIEW_PROMPT,
        "is_custom": template != quality.DEFAULT_REVIEW_PROMPT,
        "placeholder": quality.REQUEST_PLACEHOLDER,
        "max_bytes": MAX_TEMPLATE_BYTES,
        **prompt_meta(ctx, template),
    }


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".incoming")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_prompt(ctx, body: QualityPromptBody):
    text = (getattr(body, "template", "") or "").strip()
    if len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise HTTPException(422, "Quality prompt is too large.")
    try:
        quality.validate_template(text)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with _LOCK:
        _atomic_write(_path(ctx), text + "\n")
    ctx.record_audit(
        "quality.prompt_saved",
        {"bytes": len(text.encode("utf-8")), "hash": hashlib.sha256(text.encode()).hexdigest()[:12]},
    )
    return template_info(ctx)


def reset_prompt(ctx):
    path = _path(ctx)
    with _LOCK:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    ctx.record_audit("quality.prompt_reset", {})
    return template_info(ctx)
