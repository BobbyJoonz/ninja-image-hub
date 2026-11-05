import asyncio
import json

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from .schemas import LoginClick, LoginType, LoginKey


async def ensure_worker_awake(ctx, acc, timeout=12.0):
    """Wake an idle-reaped browser before a remote-login request.

    The browser is normally stopped when the account is idle (RAM saving). Opening the
    login page must start it again instead of returning "worker unavailable".
    """
    ctx.touch_account(acc["id"])
    if not ctx.runtime_installed():
        return False
    if ctx.process_state(acc) == "running" and ctx.upstream_probe(acc):
        return True
    ctx.wake_worker_async(acc)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.5)
        if ctx.process_state(acc) == "running" and ctx.upstream_probe(acc):
            ctx.touch_account(acc["id"])
            return True
    return False


def login_page(ctx, account_id: str, request: Request):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    # Opening the login page is intent to use the browser: wake it if it was idle-reaped.
    ctx.touch_account(account_id)
    if ctx.process_state(acc) != "running":
        ctx.wake_worker_async(acc)
    import html

    title = html.escape(acc["name"])
    csrf = json.dumps(request.session["csrf"])
    base = json.dumps(f"/api/accounts/{account_id}/remote")
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect {title} · Ninja Image Hub</title>\n<style>body{{background:#080f1e;color:#e8eefb;font-family:system-ui;margin:0;padding:24px}}main{{max-width:1250px;margin:auto}}header{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}h2{{margin:0 0 6px}}p{{color:#9caccc}}button{{border:1px solid #3b506f;background:#172940;color:white;border-radius:9px;padding:11px 15px;cursor:pointer}}button:hover{{background:#284267}}input{{flex:1;min-width:230px;background:#111f32;border:1px solid #3b506f;color:#fff;border-radius:9px;padding:12px}}.toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}.screen{{display:block;width:100%;max-width:1280px;border:1px solid #3b506f;border-radius:14px;background:#15243a;cursor:crosshair}}#status{{margin:12px 0;color:#9caccc}}.notice{{padding:13px;background:#152137;border:1px solid #2c496f;border-radius:10px}}.green{{color:#68dfbe}}</style></head><body><main><header><div><h2>Connect: {title}</h2><p>Private browser session · Manual login · Session saved locally</p></div><button onclick="window.close()">Close</button></header><p class="notice">Only sign in to an account you own. Do not share this page. Your credentials are typed directly into the worker browser; this panel does not store them. Do not use this on a publicly exposed HTTP connection.</p><div class="toolbar"><button onclick="navigate()">Open ChatGPT</button><button onclick="pressKey('Tab')">Tab</button><button onclick="pressKey('Enter')">Enter</button><button onclick="pressKey('Escape')">Esc</button><button onclick="save()">Save Session</button><button onclick="refresh()">Refresh</button></div><div class="toolbar"><input id="type" type="password" placeholder="Type into focused browser field (hidden by default)" autocomplete="off"><button onclick="typeNow()">Type</button><button onclick="toggleShow()">Show / Hide</button></div><div id="status">Initializing browser session…</div><img id="screen" class="screen" alt="Live browser screenshot" onclick="clickScreen(event)"></main><script>\nconst BASE={base}, CSRF={csrf};let busy=false;\nasync function request(path,body){{const opts={{method:'POST',headers:{{'content-type':'application/json','x-csrf-token':CSRF}},body:JSON.stringify(body||{{}})}};const res=await fetch(BASE+'/'+path,opts);const data=await res.json();if(!res.ok)throw Error(data.detail||data.error||'Request failed');return data;}}\nfunction msg(t){{document.getElementById('status').textContent=t;}}\nasync function refresh(){{try{{const r=await fetch(BASE+'/screenshot?ts='+Date.now(),{{cache:'no-store'}});if(!r.ok)throw Error('Worker unavailable or login session not opened');const b=await r.blob();let im=document.getElementById('screen');if(im.dataset.url)URL.revokeObjectURL(im.dataset.url);im.dataset.url=URL.createObjectURL(b);im.src=im.dataset.url;msg('Browser screenshot updated');}}catch(e){{msg(e.message);}}}}\nasync function action(path,body){{try{{msg('Working…');await request(path,body);setTimeout(refresh,550);}}catch(e){{msg(e.message);}}}}\nfunction navigate(){{action('navigate',{{url:'https://chatgpt.com'}});}} function pressKey(key){{action('key',{{key}});}}\nfunction toggleShow(){{const e=document.getElementById('type');e.type=e.type==='password'?'text':'password';}}\nfunction typeNow(){{const el=document.getElementById('type'),v=el.value;if(!v)return;el.value='';action('type',{{text:v}});}}\ndocument.getElementById('type').addEventListener('keydown',e=>{{if(e.key==='Enter')typeNow();}});\nfunction clickScreen(e){{const rect=e.target.getBoundingClientRect();action('click',{{x:Math.round((e.clientX-rect.left)/rect.width*1280),y:Math.round((e.clientY-rect.top)/rect.height*800)}});}}\nasync function save(){{try{{await request('save');msg('Session saved. Return to Accounts and refresh status.');}}catch(e){{msg(e.message);}}}}\nasync function init(){{try{{await fetch(BASE+'/init',{{cache:'no-store'}});}}catch(e){{}}refresh();}}init();setInterval(refresh,4000);\n</script></body></html>""",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self' blob:; img-src 'self' blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline' 'self'; connect-src 'self'",
        },
    )


async def upstream_login_call(ctx, account_id, action, data=None):
    acc = ctx.query_account(account_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    if not ctx.runtime_installed():
        raise HTTPException(
            409, "Install gateway runtime first: python scripts/prepare_gateway.py"
        )
    if not await ensure_worker_awake(ctx, acc):
        raise HTTPException(
            409,
            "Worker is starting; wait a few seconds and press Refresh.",
        )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=45) as client:
            headers = {"Authorization": f"Bearer {acc['api_secret']}"}
            url = (
                ctx.upstream(acc)
                + "/login"
                + ("/" + action if action != "init" else "")
            )
            method = "GET" if action in ("screenshot", "init") else "POST"
            res = await client.request(
                method, url, headers=headers, json=data if method == "POST" else None
            )
    except httpx.RequestError as e:
        raise HTTPException(503, "Gateway not ready.")
    if res.status_code >= 400:
        try:
            detail = res.json().get("error", "Upstream error")
        except ValueError:
            detail = "Upstream failed."
        raise HTTPException(res.status_code, str(detail)[:200])
    return res


async def remote_init(ctx, account_id: str):
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
        "Space",
    }:
        raise HTTPException(422, "Unsupported key.")
    await ctx.upstream_login_call(account_id, "key", body.model_dump())
    return {"ok": True}


async def remote_nav(ctx, account_id: str):
    await ctx.upstream_login_call(
        account_id, "navigate", {"url": "https://chatgpt.com"}
    )
    return {"ok": True}


async def remote_save(ctx, account_id: str):
    await ctx.upstream_login_call(account_id, "save")
    return {"ok": True}
