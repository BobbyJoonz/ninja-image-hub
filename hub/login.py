import asyncio
import threading
import html
import json

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from . import live_page as live_page_template
from . import login_page as login_page_template
from .schemas import LiveInput, LoginClick, LoginKey, LoginType

# GET /login (init) waits for the browser's exclusive lock, which a running generation
# job can hold for minutes. Fail fast with a clear message instead of hanging the page.
INIT_TIMEOUT = httpx.Timeout(20.0, connect=4.0)
ACTION_TIMEOUT = httpx.Timeout(45.0, connect=4.0)


def _display_note(ctx):
    mode = ctx.display_state()
    if mode == "xvfb":
        return (
            "This server draws the browser on a virtual display (Xvfb), so there is no "
            "window on a desk. Sign in through this page."
        )
    if mode == "missing":
        return (
            "No browser display is available on this host. Install xvfb or run the panel "
            "with a real DISPLAY before starting workers."
        )
    if mode == "x11":
        return (
            "An X11 display is available on the server; you may also sign in through the "
            "worker window if you can see it."
        )
    return (
        "The worker opens its own Chrome window on this machine; you can also sign in "
        "there and then press Refresh status in the panel."
    )


async def ensure_worker_awake(ctx, acc, timeout=15.0):
    """Make sure a healthy worker exists before a remote-login request (waking it if idle)."""
    if not ctx.runtime_installed():
        raise HTTPException(
            409,
            "Gateway runtime is not installed. Run python scripts/prepare_gateway.py.",
        )
    ctx.touch_account(acc["id"])
    if ctx.process_state(acc) == "running" and ctx.upstream_probe(acc):
        return
    ctx.wake_worker_async(acc)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.5)
        if ctx.process_state(acc) == "running" and ctx.upstream_probe(acc):
            ctx.touch_account(acc["id"])
            return
    raise HTTPException(
        409,
        "The browser did not start within 15 seconds. Check Accounts → Logs and run "
        "scripts/doctor.py, then press Open ChatGPT again.",
    )


def login_page(ctx, account_id: str, request: Request):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    # Opening the login page is explicit intent to use the browser: wake it if the idle
    # reaper stopped it. (An operator Stop is also overridden here on purpose.)
    ctx.touch_account(account_id)
    if ctx.process_state(acc) != "running":
        ctx.wake_worker_async(acc)
    csrf = request.session.get("csrf")
    if not csrf:
        raise HTTPException(401, "Panel session changed; sign in again.")
    return HTMLResponse(
        login_page_template.render(
            account_id=account_id,
            title=html.escape(acc["name"]),
            csrf_js=json.dumps(csrf),
            display_note=html.escape(_display_note(ctx)),
        ),
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self' blob:; img-src 'self' blob:; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline' 'self'; connect-src 'self'"
            ),
        },
    )


async def upstream_login_call(ctx, account_id, action, data=None):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    await ensure_worker_awake(ctx, acc)
    timeout = INIT_TIMEOUT if action == "init" else ACTION_TIMEOUT
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            headers = {"Authorization": f"Bearer {acc['api_secret']}"}
            url = (
                ctx.upstream(acc) + "/login" + ("/" + action if action != "init" else "")
            )
            method = "GET" if action in ("screenshot", "init", "url", "pages", "plan", "workspace", "settings") else "POST"
            res = await client.request(
                method, url, headers=headers, json=data if method == "POST" else None
            )
    except httpx.TimeoutException:
        if action == "init":
            raise HTTPException(
                409,
                "The browser is busy with a running generation job. The login session "
                "opens when it finishes; retrying automatically.",
            )
        raise HTTPException(
            504, "The gateway did not answer in time. Press Refresh and try again."
        )
    except httpx.RequestError as exc:
        raise HTTPException(503, "Gateway not ready: " + str(exc)[:120])
    if res.status_code >= 400:
        try:
            detail = res.json().get("error", "Upstream error")
        except ValueError:
            detail = "Upstream failed."
        if action == "screenshot" and res.status_code == 409:
            raise HTTPException(409, "login session is not active — press Open ChatGPT")
        raise HTTPException(res.status_code, str(detail)[:200])
    return res


async def remote_init(ctx, account_id: str):
    """Open (or reuse) the gateway's manual-login session. One retry for a lease race."""
    try:
        await ctx.upstream_login_call(account_id, "init")
    except HTTPException as first:
        if first.status_code not in (409, 503):
            raise
        await asyncio.sleep(0.6)
        await ctx.upstream_login_call(account_id, "init")
    return {"ok": True}


async def remote_shot(ctx, account_id: str):
    r = await ctx.upstream_login_call(account_id, "screenshot")
    return Response(
        content=r.content,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def remote_click(ctx, account_id: str, body: LoginClick):
    await ctx.upstream_login_call(account_id, "click", body.model_dump())
    return {"ok": True}


async def remote_type(ctx, account_id: str, body: LoginType):
    await ctx.upstream_login_call(account_id, "type", body.model_dump())
    return {"ok": True}


async def remote_key(ctx, account_id: str, body: LoginKey):
    if body.key not in {
        "Tab",
        "Enter",
        "Escape",
        "Backspace",
        "ArrowDown",
        "ArrowUp",
        "ArrowLeft",
        "ArrowRight",
        "Space",
        "PageDown",
        "PageUp",
        "Home",
        "End",
    }:
        raise HTTPException(422, "Unsupported key.")
    await ctx.upstream_login_call(account_id, "key", body.model_dump())
    return {"ok": True}


async def remote_nav(ctx, account_id: str):
    await ctx.upstream_login_call(account_id, "navigate", {"url": "https://chatgpt.com"})
    return {"ok": True}


def _identity_in_background(ctx, account_id):
    """After sign-in, read the real e-mail/workspace without blocking the page."""

    def run():
        try:
            account = ctx.query_account(account_id)
            if not account:
                return
            plan = ctx.plan_snapshot(account)
            if plan:
                ctx.save_detected_plan(account_id, plan)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


async def remote_save(ctx, account_id: str):
    """Persist the browser session, then report whether the gateway confirms it.

    Saving closes the manual-login session upstream, so the page must not keep polling;
    the panel also gets an immediate verdict instead of waiting for the 5-minute watchdog.
    """
    await ctx.upstream_login_call(account_id, "save")
    acc = ctx.query_account(account_id)
    session = "unknown"
    blocker = None
    if acc:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            snapshot = await asyncio.to_thread(ctx.session_snapshot, acc)
            session = snapshot["state"]
            blocker = snapshot.get("blocker")
            if session == "connected":
                break
            await asyncio.sleep(0.7)
    notes = {
        "connected": "Gateway confirms the session. Send a test job.",
        "unverified": (
            "Cookies are saved but not confirmed yet. Send a test job; if it fails, "
            "reopen the login page and sign in again."
        ),
        "unknown": "Session state unknown. Send a test job to verify.",
    }
    _identity_in_background(ctx, account_id)
    return {
        "ok": True,
        "session": session,
        "blocker": blocker,
        "note": notes.get(session, notes["unknown"]),
    }

async def remote_url(ctx, account_id: str):
    """Where the remote login page currently points (diagnostics / project discovery)."""
    response = await ctx.upstream_login_call(account_id, "url")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Gateway returned an invalid diagnostics payload.")


async def remote_pages(ctx, account_id: str):
    """Every page open in the worker context: proves where generation chats land."""
    response = await ctx.upstream_login_call(account_id, "pages")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Gateway returned an invalid diagnostics payload.")


async def remote_plan_probe(ctx, account_id: str):
    """Raw (sanitised) plan payload from the signed-in browser: discovery endpoint."""
    response = await ctx.upstream_login_call(account_id, "plan")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Gateway returned an invalid plan payload.")


async def remote_plan(ctx, account_id: str):
    """Fetch the plan ChatGPT reports for this account and cache it."""
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    plan = await asyncio.to_thread(ctx.plan_snapshot, acc)
    if not plan:
        raise HTTPException(
            502,
            "Could not read the plan from ChatGPT. Make sure the account is signed in "
            "and the worker is online, then try again.",
        )
    ctx.save_detected_plan(account_id, plan)
    return plan


async def remote_settings_probe(ctx, account_id: str):
    """Raw Settings -> Subscription text as rendered in the worker browser."""
    response = await ctx.upstream_login_call(account_id, "settings")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Gateway returned an invalid settings payload.")

def live_page(ctx, account_id: str, request: Request):
    """Fast live view: CDP screencast with a real cursor and trusted input."""
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    ctx.touch_account(account_id)
    if ctx.process_state(acc) != "running":
        ctx.wake_worker_async(acc)
    csrf = request.session.get("csrf")
    if not csrf:
        raise HTTPException(401, "Panel session changed; sign in again.")
    return HTMLResponse(
        live_page_template.render(
            account_id=account_id,
            title=html.escape(acc["name"]),
            csrf=json.dumps(csrf),
            classic_url=f"/accounts/{account_id}/login",
        ),
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


async def live_stream(ctx, account_id: str, request: Request):
    """Proxy the gateway's MJPEG screencast to the panel (single stream per viewer)."""
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    ctx.touch_account(account_id)
    mode = "screencast" if request.query_params.get("mode") == "screencast" else "poll"
    quality = request.query_params.get("q") or "fast"
    if quality not in ("fast", "balanced", "sharp"):
        quality = "fast"
    url = ctx.upstream(acc) + "/login/live?mode=" + mode + "&q=" + quality
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30.0, read=None))
    try:
        request = client.build_request(
            "GET", url, headers={"Authorization": f"Bearer {acc['api_secret']}"}
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(503, "Gateway not ready: " + str(exc)[:100])
    if response.status_code >= 400:
        detail = "Live view unavailable."
        try:
            detail = (await response.aread()).decode("utf-8", "replace")[:200] or detail
        except Exception:
            pass
        await response.aclose()
        await client.aclose()
        raise HTTPException(response.status_code, detail)
    if "multipart/x-mixed-replace" not in response.headers.get("content-type", ""):
        await response.aclose()
        await client.aclose()
        raise HTTPException(502, "Gateway did not start a screencast.")

    async def frames():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def live_input(ctx, account_id: str, body: LiveInput):
    """Forward batched mouse/keyboard events as trusted CDP input."""
    events = []
    for event in body.events[:200]:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "mouse":
            events.append(
                {
                    "type": "mouse",
                    "action": event.get("action"),
                    "x": int(event.get("x") or 0),
                    "y": int(event.get("y") or 0),
                    "button": event.get("button"),
                    "buttons": int(event.get("buttons") or 0),
                    "deltaX": float(event.get("deltaX") or 0),
                    "deltaY": float(event.get("deltaY") or 0),
                }
            )
        elif kind == "key":
            events.append(
                {"type": "key", "key": str(event.get("key") or "")[:32], "vk": int(event.get("vk") or 0)}
            )
        elif kind == "text":
            events.append({"type": "text", "text": str(event.get("text") or "")[:500]})
    if not events:
        return {"ok": True, "applied": 0}
    await ctx.upstream_login_call(account_id, "input", {"events": events})
    return {"ok": True, "applied": len(events)}

async def live_cursor(ctx, account_id: str, x: int = 0, y: int = 0):
    """Ask the worker page what cursor/affordance it would show at this point."""
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    ctx.touch_account(account_id)
    url = ctx.upstream(acc) + f"/login/cursor?x={int(x)}&y={int(y)}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=8.0) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {acc['api_secret']}"}
            )
    except httpx.RequestError:
        raise HTTPException(503, "Gateway not ready.")
    if response.status_code != 200:
        raise HTTPException(response.status_code, "Cursor probe failed.")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "Invalid cursor payload.")


async def live_focus(ctx, account_id: str):
    """Make sure the worker window is not minimized before streaming/clicking."""
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    ctx.touch_account(account_id)
    restored = bool(ctx.focus_worker_window(acc)) if hasattr(ctx, "focus_worker_window") else False
    return {"ok": True, "restored": restored, "platform": "win32" if restored else "other"}
