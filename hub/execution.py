"""One job execution: references, upstream call, honest outcome, cleanup.

Kept separate from dispatch/supervision so the failure classification and cleanup logic can
be tested without starting the scheduler loop.
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select, update

from . import effort as effort_tier
from .gateway import request_generation, ResponseBudgetError
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
            upload_id = conn.execute(
                select(ctx.jobs.c.reference_upload_id).where(
                    ctx.jobs.c.id == job_id
                )
            ).scalar_one_or_none()
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
