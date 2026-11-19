"""One job execution: references, upstream call, honest outcome, cleanup.

Kept separate from dispatch/supervision so the failure classification and cleanup logic can
be tested without starting the scheduler loop.
"""

import hashlib
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select, update

from . import effort as effort_tier
import json

from . import quality_gate as quality
from . import text_overlay as typography
from .gateway import request_chat, request_edit, request_generation, ResponseBudgetError
from .gateway_errors import gateway_failure_text, parse_gateway_failure


def execute_job(ctx, job_id, acc, prompt, aspect, quality_preference, n=1):
    """Run once. No automatic replay after uncertain upstream outcomes."""
    upload = None
    upload_id = None
    handles = []
    try:
        if ctx.process_state(acc) != "running" or not ctx.upstream_probe(acc):
            # Nothing was submitted upstream, so returning the job to the queue is safe.
            # Hard-failing here silently dropped a job the provider never saw.
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id, ctx.jobs.c.status == "running")
                    .values(status="queued", started_at=None)
                )
            return
        if quality_preference == "best":
            prompt += "\n\nProduction guidance: preserve requested details faithfully; aim for clean edges, intentional composition, readable text when requested, and a polished final result. Do not add unrelated objects."
        # Reference images: read from the private upload workspace. An expired upload is a
        # pre-send failure (nothing was submitted), so failing the job is honest and safe.
        # The upload id is read from the job row so execute_job keeps its historical
        # signature (tests and callbacks patch it with exactly these arguments).
        with ctx.engine.connect() as conn:
            job_row = conn.execute(
                select(
                    ctx.jobs.c.reference_upload_id, ctx.jobs.c.quality_rounds,
                    ctx.jobs.c.text_overlay,
                ).where(ctx.jobs.c.id == job_id)
            ).first()
        upload_id = job_row[0] if job_row else None
        quality_rounds = int((job_row[1] if job_row else 0) or 0)
        overlay_enabled = bool(job_row[2]) if job_row else False
        overlay_texts = typography.extract_literals(prompt) if overlay_enabled else []
        files = None
        if upload_id:
            upload = ctx.load_upload(upload_id)
            if not upload:
                with ctx.engine.begin() as conn:
                    conn.execute(
                        update(ctx.jobs)
                        .where(ctx.jobs.c.id == job_id)
                        .values(
                            status="failed",
                            error="Reference images expired before dispatch; nothing was sent upstream.",
                            finished_at=ctx.now(),
                        )
                    )
                return
            files = []
            for item in upload["files"]:
                handle = open(item["path"], "rb")
                handles.append(handle)
                files.append(
                    (
                        "reference_images",
                        (
                            item.get("safe_name") or item.get("name") or "reference",
                            handle,
                            item.get("mime") or "image/png",
                        ),
                    )
                )
        # Reasoning effort is a real ChatGPT web-UI control ("Thinking effort"), not a
        # prompt hint. We always ask for the configured tier (High by default) and read
        # back what the UI actually applied; a downgrade is recorded and surfaced but the
        # delivered image is never discarded or retried.
        thinking_effort = ctx.setting("thinking_effort")
        thinking_mode = effort_tier.gateway_mode(thinking_effort)
        if overlay_texts:
            prompt = typography.no_text_guidance(prompt, overlay_texts)
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect,
            "n": n,
            "response_format": "url",
        }
        if thinking_mode:
            payload["thinking_mode"] = thinking_mode
        try:
            url = ctx.upstream(acc) + "/v1/images/generations"
            if files:
                r = request_generation(
                    url, acc["api_secret"], payload, n, files=files
                )
            else:
                # Keep the no-reference path a plain 4-argument call: existing tests and
                # integrations monkeypatch request_generation with that signature.
                r = request_generation(url, acc["api_secret"], payload, n)
        except (
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            ResponseBudgetError,
        ) as e:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(
                        status="unknown",
                        error="Response lost after request submission; upstream outcome unknown. Check manually. Do not auto-retry.",
                        finished_at=ctx.now(),
                    )
                )
            return
        if r.status_code >= 500:
            failure = parse_gateway_failure(r.status_code, r.data)
            detail = gateway_failure_text(failure)
            state = "failed" if failure["certain_failure"] else "unknown"
            print(
                f"[gateway] job {str(job_id)[:8]} HTTP {r.status_code} kind={failure['kind'] or '?'} -> {state}",
                file=sys.stderr,
            )
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(status=state, error=detail, finished_at=ctx.now())
                )
            return
        if r.status_code >= 400:
            failure = parse_gateway_failure(r.status_code, r.data)
            detail = (
                failure["message"]
                or f"Gateway rejected the image request ({failure['kind'] or 'HTTP ' + str(r.status_code)})."
            )
            if failure["blocker"]:
                detail += f" [login blocker: {failure['blocker']}]"
            kind = failure["kind"]
            if r.status_code == 429 or kind == "rate_limit":
                retry_after = r.headers.get("retry-after", "")
                try:
                    extra_seconds = max(0, int(retry_after))
                except (ValueError, TypeError):
                    try:
                        extra_seconds = max(
                            0,
                            (
                                parsedate_to_datetime(retry_after).astimezone(
                                    timezone.utc
                                )
                                - datetime.now(timezone.utc)
                            ).total_seconds(),
                        )
                    except (ValueError, TypeError, OverflowError):
                        extra_seconds = 0
                cool_seconds = max(
                    ctx.setting("provider_cooldown_minutes") * 60, extra_seconds
                )
                until = (
                    datetime.now(timezone.utc) + timedelta(seconds=cool_seconds)
                ).isoformat()
                with ctx.engine.begin() as conn:
                    conn.execute(
                        update(ctx.accounts)
                        .where(ctx.accounts.c.id == acc["id"])
                        .values(cooldown_until=until)
                    )
                raise ctx.ProviderLimit(
                    f"Provider rate-limited this account; paused until {until}. No automatic retry."
                )
            raise RuntimeError(str(detail)[:250])
        try:
            response_body = r.data
            if not isinstance(response_body, dict):
                raise ValueError("Invalid gateway response")
        except ValueError:
            with ctx.engine.begin() as conn:
                conn.execute(
                    update(ctx.jobs)
                    .where(ctx.jobs.c.id == job_id)
                    .values(
                        status="unknown",
                        error="Gateway response was unreadable after successful request; outcome is unknown.",
                        finished_at=ctx.now(),
                    )
                )
            return
        quality_meta = None
        quality_score = None
        quality_verdict = None
        applied = response_body.get("applied") or {}
        conversation_id = applied.get("conversation_id")
        conversation_url = applied.get("conversation_url")
        if (int(ctx.setting("quality_gate") or 0) > 0 or quality_rounds > 0) and (
            conversation_url or conversation_id
        ):
            response_body, quality_meta = _apply_quality_gate(
                ctx, acc, prompt, conversation_id, conversation_url,
                response_body, payload, url, thinking_mode, n,
                rounds=quality_rounds,
            )
            if quality_meta:
                quality_score = quality_meta.get("score")
                quality_verdict = quality_meta.get("verdict")
                conversation_id = quality_meta.get("conversation_id") or conversation_id
        if overlay_texts:
            _apply_text_overlay(ctx, response_body, overlay_texts, job_id)
        stored, save_error = ctx.record_image_batch(
            response_body, job_id, acc["port"], n
        )
        effort_summary = effort_tier.summarize(
            response_body.get("applied"), thinking_effort
        )
        if effort_summary["degraded"] or effort_summary["verified"] is False:
            print(
                f"[effort] account {str(acc['id'])[:8]}: requested "
                f"{effort_summary['requested']}, applied "
                f"{effort_summary['applied'] or 'unknown'} "
                f"(verified={effort_summary['verified']}, mixed={effort_summary['mixed']}); "
                "image kept, nothing retried",
                file=sys.stderr,
            )
        status = (
            "completed"
            if len(stored) == n and (not save_error)
            else "partial" if stored else "failed"
        )
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(
                    status=status,
                    finished_at=ctx.now(),
                    error=save_error
                    or (
                        f"Only {len(stored)} of {n} requested images returned. No automatic replay."
                        if len(stored) < n
                        else None
                    ),
                    thinking_requested=effort_summary["requested"],
                    thinking_applied=effort_summary["applied"],
                    thinking_verified=(
                        None
                        if effort_summary["verified"] is None
                        else int(effort_summary["verified"])
                    ),
                    thinking_fallback=effort_summary["fallback"] or None,
                    thinking_mixed=1 if effort_summary["mixed"] else 0,
                    conversation_id=conversation_id,
                    quality_score=quality_score,
                    quality_verdict=quality_verdict,
                    quality_meta=(
                        _quality_meta_json(quality_meta) if quality_meta else None
                    ),
                )
            )
    except ctx.ProviderLimit as e:
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(
                    status="rate_limited", error=str(e)[:350], finished_at=ctx.now()
                )
            )
    except Exception as e:
        with ctx.engine.begin() as conn:
            conn.execute(
                update(ctx.jobs)
                .where(ctx.jobs.c.id == job_id)
                .values(status="failed", error=str(e)[:350], finished_at=ctx.now())
            )
    finally:
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass
        if upload_id:
            try:
                with ctx.engine.connect() as conn:
                    state = conn.execute(
                        select(ctx.jobs.c.status).where(ctx.jobs.c.id == job_id)
                    ).scalar_one_or_none()
                if state in (
                    "completed",
                    "partial",
                    "failed",
                    "rate_limited",
                    "canceled",
                ):
                    ctx.release_upload(upload_id)
            except Exception:  # noqa: BLE001 - cleanup must never mask the job outcome
                pass
        with ctx.queue_guard:
            ctx.active_jobs.discard(job_id)
            ctx.active_per_account[acc["id"]] = max(
                0, ctx.active_per_account[acc["id"]] - 1
            )
            ctx.queue_wakeup.set()
        # A finished job counts as recent activity: do not reap the browser in the same
        # minute the account just produced an image.
        ctx.touch_account(acc["id"])

def _apply_quality_gate(ctx, acc, prompt, conversation_id, conversation_url, body,
                        payload, url, thinking_mode, n, rounds=0):
    """Quality Loop: review the image in its own chat and regenerate up to ``rounds`` times.

    A per-job ``rounds`` value (1..3) opts this job in even when the global gate is off.
    Each round = one correction generation in the same conversation, followed by a fresh
    review; only an improved image replaces the current one. Never raises, never loops
    indefinitely, and never fails the job.
    """
    mode = int(ctx.setting("quality_gate") or 0)
    threshold = int(ctx.setting("quality_threshold") or 8)
    refines_allowed = max(0, min(3, rounds if rounds > 0 else (1 if mode >= 2 else 0)))
    meta = {
        "quality_loop": True,
        "mode": mode,
        "rounds_requested": rounds,
        "refines_allowed": refines_allowed,
        "threshold": threshold,
        "conversation_id": conversation_id,
        "conversation_url": conversation_url,
        "history": [],
        "original_urls": [item.get("url") for item in (body.get("data") or []) if item.get("url")],
    }
    try:
        url_chat = ctx.upstream(acc) + "/v1/chat/completions"
        current = body
        template = (
            ctx.quality_prompt_template()
            if hasattr(ctx, "quality_prompt_template")
            else None
        )
        current_review = quality.parse_review(
            _review_chat(
                url_chat, acc, conversation_id, conversation_url,
                quality.build_review_prompt(prompt, template),
            )
        )
        meta["history"].append(_history_entry(0, current_review))
        best_body, best_review = body, current_review
        for attempt in range(1, refines_allowed + 1):
            if not quality.needs_refinement(current_review, threshold):
                break
            correction = quality.build_correction_prompt(
                prompt, current_review.get("issues")
            )
            refined_payload = dict(payload)
            refined_payload["prompt"] = correction
            refined_payload["conversation_id"] = conversation_id
            if conversation_url:
                refined_payload["conversation_url"] = conversation_url
            refined = None
            refine_method = None
            same_chat_problem = "same-chat regeneration unavailable"
            try:
                same_chat = request_generation(url, acc["api_secret"], refined_payload, n)
            except Exception as exc:  # noqa: BLE001 - fall through to the edit path
                meta["refine_same_chat_error"] = str(exc)[:160]
                same_chat = None
            if (
                same_chat is not None
                and same_chat.status_code == 200
                and isinstance(same_chat.data, dict)
            ):
                same_chat_problem = _refinement_problem(body, same_chat.data)
                if same_chat_problem is None:
                    refined, refine_method = same_chat, "same_chat"
            elif same_chat is not None:
                same_chat_problem = f"same-chat HTTP {same_chat.status_code}"
                meta["refine_same_chat_error"] = same_chat_problem
            if refined is None:
                # Reused chats can answer without rendering a NEW image (the DOM still
                # holds the old one). A real image edit always renders one from the
                # attached image, so use it as the deterministic repair path.
                edited = _edit_refinement(
                    ctx, acc, body, prompt, current_review, thinking_mode
                )
                if edited is not None:
                    edited_response, edit_prompt_used = edited
                    edit_problem = _refinement_problem(body, edited_response.data)
                    if edit_problem is None:
                        refined, refine_method = edited_response, "edit"
                        meta["edit_prompt"] = edit_prompt_used[:300]
                    else:
                        meta["refine_edit_error"] = edit_problem
            if refined is None:
                meta["error"] = (
                    meta.get("refine_edit_error")
                    or same_chat_problem
                    or "refinement did not produce a new image; original kept"
                )
                meta["refined"] = False
                break
            meta["refine_method"] = refine_method
            refined_applied = refined.data.get("applied") or {}
            latest_url = refined_applied.get("conversation_url") or conversation_url
            if latest_url:
                conversation_url = latest_url
                meta["conversation_url"] = latest_url
            try:
                refined_review = quality.parse_review(
                    _review_chat(
                        url_chat, acc, conversation_id, conversation_url,
                        quality.build_review_prompt(prompt, template),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                refined_review = None
            meta["history"].append(_history_entry(attempt, refined_review))
            if refined_review and quality.improved(current_review, refined_review):
                current = refined.data
                current_review = refined_review
                best_body, best_review = refined.data, refined_review
                meta["refined"] = True
                meta["refined_urls"] = [
                    item.get("url")
                    for item in (refined.data.get("data") or [])
                    if item.get("url")
                ]
            else:
                break
        meta["attempts"] = len(meta["history"]) - 1
        meta["score"] = (best_review or {}).get("overall")
        meta["verdict"] = (best_review or {}).get("verdict")
        meta["review"] = best_review
        meta["refined"] = bool(meta.get("refined"))
        return best_body, meta
    except Exception as exc:  # noqa: BLE001 - advisory only
        meta["error"] = str(exc)[:200]
        return body, meta


def _history_entry(attempt, review):
    if not review:
        return {"attempt": attempt, "error": "review unavailable"}
    return {
        "attempt": attempt,
        "overall": review.get("overall"),
        "text": review.get("text_score"),
        "numbers": review.get("numbers_score"),
        "verdict": review.get("verdict"),
        "issues": (review.get("issues") or [])[:5],
    }


def _review_chat(url_chat, acc, conversation_id, conversation_url, prompt):
    """Ask the model to audit the image it just produced (same conversation).

    ``conversation_url`` is the project-aware page URL (/g/<project>/c/<id>) captured
    from the generation turn; the UUID alone 404s for chats inside a project.
    """
    payload = {"messages": [{"role": "user", "content": prompt}]}
    if conversation_url:
        payload["conversation_url"] = conversation_url
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = request_chat(
        url_chat,
        acc["api_secret"],
        payload,
        timeout=180.0,
    )
    if response.status_code != 200 or not isinstance(response.data, dict):
        raise RuntimeError(f"review HTTP {response.status_code}")
    choices = response.data.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        content = ((choices[0].get("message") or {}).get("content") or "")
    if not content:
        raise RuntimeError("review returned no text")
    return content


def _quality_meta_json(meta, limit=3900):
    """Serialise quality metadata without ever writing truncated (invalid) JSON."""

    def compact(value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        history = out.get("history")
        if isinstance(history, list):
            out["history"] = [
                {
                    key: entry.get(key)
                    for key in ("attempt", "overall", "text", "numbers", "verdict")
                    if entry.get(key) is not None
                }
                | ({"issues": (entry.get("issues") or [])[:3]} if entry.get("issues") else {})
                for entry in history[:4]
            ]
        for key in ("review", "refined_review"):
            review = out.get(key)
            if isinstance(review, dict):
                out[key] = {
                    field: review.get(field)
                    for field in (
                        "overall", "text_score", "numbers_score", "spelling_score",
                        "layout_score", "verdict",
                    )
                    if review.get(field) is not None
                } | ({"issues": (review.get("issues") or [])[:3]} if review.get("issues") else {})
        for key in ("original_urls", "refined_urls"):
            urls = out.get(key)
            if isinstance(urls, list):
                out[key] = urls[:10]
        return out

    payload = compact(meta)
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) <= limit:
        return encoded
    payload.pop("history", None)
    payload.pop("review", None)
    payload.pop("refined_review", None)
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) <= limit:
        return encoded
    fallback = {
        key: payload.get(key)
        for key in ("quality_loop", "mode", "rounds_requested", "refines_allowed",
                    "threshold", "conversation_id", "conversation_url", "attempts",
                    "score", "verdict", "refined", "error")
        if payload.get(key) is not None
    }
    fallback["truncated"] = True
    encoded = json.dumps(fallback, ensure_ascii=False)
    return encoded[:limit] if len(encoded) <= limit else json.dumps(
        {"truncated": True, "score": payload.get("score"), "verdict": payload.get("verdict")}
    )


def _image_digest(url):
    """sha256 of a gateway-local image URL, or None when it cannot be verified."""
    try:
        with httpx.Client(
            trust_env=False, timeout=httpx.Timeout(read=30, connect=5)
        ) as client:
            response = client.get(url)
    except Exception:  # noqa: BLE001 - verification is best-effort
        return None
    if response.status_code != 200 or not response.content:
        return None
    return hashlib.sha256(response.content).hexdigest()


def _same_image_bytes(original_body, refined_body):
    """True when every refined image has the same bytes as the original; None unknown."""
    original = [item.get("url") for item in (original_body.get("data") or [])]
    refined = [item.get("url") for item in (refined_body.get("data") or [])]
    if not original or not refined or len(original) != len(refined):
        return None
    original_digests = [_image_digest(url) for url in original]
    refined_digests = [_image_digest(url) for url in refined]
    if any(digest is None for digest in original_digests + refined_digests):
        return None
    return original_digests == refined_digests


def _download_image_bytes(url):
    """Fetch a gateway-local image once; returns (bytes, mime) or None."""
    try:
        with httpx.Client(
            trust_env=False, timeout=httpx.Timeout(read=60, connect=5)
        ) as client:
            response = client.get(url)
    except Exception:  # noqa: BLE001
        return None
    if response.status_code != 200 or not response.content:
        return None
    mime = (response.headers.get("content-type") or "image/png").split(";")[0].strip()
    return response.content, mime


def _refinement_problem(original_body, refined_body):
    """Why a refinement result must not replace the original (or None when okay)."""
    if not isinstance(refined_body, dict):
        return "invalid refinement response"
    original_count = len(original_body.get("data") or [])
    refined_count = len(refined_body.get("data") or [])
    if refined_body.get("partial") or refined_count < original_count:
        return f"refinement returned {refined_count}/{original_count} images"
    if _same_image_bytes(original_body, refined_body) is True:
        return "refinement returned identical image bytes"
    return None


def _edit_refinement(ctx, acc, body, prompt, review, thinking_mode):
    """Real image edit with the original image as input (new image guaranteed)."""
    if len(body.get("data") or []) != 1:
        return None
    url = (body["data"][0] or {}).get("url")
    if not url:
        return None
    downloaded = _download_image_bytes(url)
    if not downloaded:
        return None
    raw, mime = downloaded
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    edit_prompt = quality.build_edit_prompt(prompt, (review or {}).get("issues") or [])
    try:
        response = request_edit(
            ctx.upstream(acc) + "/v1/images/edits",
            acc["api_secret"],
            raw,
            mime,
            f"original.{ext}",
            edit_prompt,
            thinking_mode=thinking_mode,
        )
    except Exception:  # noqa: BLE001
        return None
    if response.status_code != 200 or not isinstance(response.data, dict):
        return None
    return response, edit_prompt


def _apply_text_overlay(ctx, body, texts, job_id):
    """Compose guaranteed text into the gateway response before storage.

    Composed bytes travel as ``b64_json`` so the normal storage path keeps its
    anti-SSRF URL checks; the raw model image is kept as an audit copy.
    """
    import base64

    items = body.get("data")
    if not isinstance(items, list):
        return
    audit = pathlib.Path(ctx.DATA) / "overlay_originals" / job_id
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        downloaded = _download_image_bytes(url)
        if not downloaded:
            continue
        raw, mime = downloaded
        composed = typography.render(raw, texts)
        try:
            audit.mkdir(parents=True, exist_ok=True)
            (audit / f"original-{position + 1}.bin").write_bytes(raw)
        except OSError:
            pass
        item.pop("url", None)
        item["b64_json"] = base64.b64encode(composed).decode("ascii")
