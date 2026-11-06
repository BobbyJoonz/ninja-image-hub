import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import httpx
from . import plans
from fastapi import HTTPException
from sqlalchemy import select
import psutil


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


def worker_rss_mb(ctx, account_id):
    with ctx.proc_guard:
        p = ctx.processes.get(account_id)
    if not p or p.poll() is not None:
        return None
    try:
        parent = psutil.Process(p.pid)
        tree = [parent] + parent.children(recursive=True)
        total = 0
        for child in tree:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return round(total / 1048576, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def upstream(ctx, acc):
    return f"http://127.0.0.1:{acc['port']}"


def runtime_installed(ctx):
    return (
        (ctx.VENDOR / "src" / "server.js").exists()
        and (ctx.VENDOR / "node_modules").is_dir()
        and (shutil.which("node") is not None)
    )


def listening(ctx, port: int):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def port_available(ctx, port):
    if ctx.listening(port):
        return False
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def allocate_port(ctx):
    with ctx.engine.connect() as c:
        used = {r[0] for r in c.execute(select(ctx.accounts.c.port))}
    for p in range(41000, 42000):
        if p not in used and ctx.port_available(p):
            return p
    raise HTTPException(503, "No free local worker port.")


def process_state(ctx, acc):
    with ctx.proc_guard:
        p = ctx.processes.get(acc["id"])
        if p is not None and p.poll() is None:
            return "running"
    return "stopped"


def upstream_probe(ctx, acc):
    try:
        r = httpx.get(trust_env=False, url=ctx.upstream(acc) + "/health", timeout=0.6)
        return r.status_code == 200
    except httpx.RequestError:
        return False


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


POPUP_FOLLOW_MARKER = "ninja:popup-follow"


def _patch_popup_follow(worker):
    """Let the remote login page follow OAuth popups (Google/Apple/phone).

    Upstream keeps one stored page and screenshots/clicks it. Clicking
    "Continue with Google" opens a popup in the same browser context, so the remote
    page stayed on the old tab and the user could not finish signing in. This small,
    guarded patch makes getLoginPage() prefer the newest open page. When upstream
    changes, the pattern simply does not match and the worker is left untouched.
    """
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if POPUP_FOLLOW_MARKER in content:
        return False
    old = (
        "async function getLoginPage() {\n"
        "  if (loginPage && !loginPage.isClosed()) return loginPage;\n"
    )
    if content.count(old) != 1:
        return False
    new = (
        "async function getLoginPage() {\n"
        "  // ninja:popup-follow — follow the newest open page so OAuth popups work.\n"
        "  try {\n"
        "    const context = await getContext();\n"
        "    const open = context.pages().filter((p) => p && !p.isClosed());\n"
        "    const newest = open[open.length - 1];\n"
        "    if (newest && newest !== loginPage) loginPage = newest;\n"
        "  } catch (err) {}\n"
        "  if (loginPage && !loginPage.isClosed()) return loginPage;\n"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


PROJECT_URL_MARKER = "ninja:project-url"


def _patch_project_url(worker):
    """Route every new ChatGPT conversation into the account's project.

    Upstream opens a new chat by navigating to the CHATGPT_URL constant. Rewriting that
    one constant to honour CHATGPT_PROJECT_URL keeps every generation inside the project
    instead of the normal chat list. Guarded and idempotent: an upstream change simply
    skips the patch.
    """
    path = pathlib.Path(worker) / "src" / "chatgpt.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if PROJECT_URL_MARKER in content:
        return False
    old = "const CHATGPT_URL = 'https://chatgpt.com';"
    if content.count(old) != 1:
        return False
    new = (
        "// ninja:project-url — new conversations open inside the configured project.\n"
        "const CHATGPT_URL = process.env.CHATGPT_PROJECT_URL || 'https://chatgpt.com';"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


PAGES_PATCH_MARKER = "ninja:pages-routes"

DIAGNOSTICS_JS = r"""// ninja:pages-routes — diagnostics for project routing.
// GET /login/url   -> URL/title of the page the remote login is currently driving.
// GET /login/pages -> every page open in the browser context (to prove where a
//                     generation chat landed: normal list /c/<id> vs project
//                     /g/g-p-.../c/<id>).
router.get('/login/url', async (req, res) => {
  try {
    await enterManualLogin({ acquire: false });
    const page = await getLoginPage();
    res.json({ url: page.url(), title: await page.title().catch(() => '') });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  }
});

router.get('/login/pages', async (req, res) => {
  try {
    await enterManualLogin({ acquire: false });
    const ctx = await getContext();
    const pages = ctx.pages().filter((page) => page && !page.isClosed());
    res.json({ pages: pages.map((page) => ({ url: page.url() })) });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  }
});

module.exports = router;"""

# The first release wrote enterManualLogin() into a variable; upgrade those copies.
BROKEN_URL_ROUTE = (
    "    const p = await enterManualLogin({ acquire: false });\n"
    "    res.json({ url: p.url(), title: await p.title().catch(() => '') });"
)
FIXED_URL_ROUTE = (
    "    await enterManualLogin({ acquire: false });\n"
    "    const page = await getLoginPage();\n"
    "    res.json({ url: page.url(), title: await page.title().catch(() => '') });"
)


def _patch_login_diagnostics(worker):
    """Expose the login page URL and every open page of the browser context.

    Lets the operator confirm that generation chats land inside the configured project
    and read the project URL after opening it. Guarded: any upstream change leaves the
    worker untouched.
    """
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if PAGES_PATCH_MARKER in content:
        if BROKEN_URL_ROUTE in content:
            path.write_text(content.replace(BROKEN_URL_ROUTE, FIXED_URL_ROUTE, 1), encoding="utf-8")
            return True
        return False
    anchor = "module.exports = router;"
    if content.count(anchor) != 1:
        return False
    path.write_text(content.replace(anchor, DIAGNOSTICS_JS, 1), encoding="utf-8")
    return True


PLAN_PROBE_MARKER = "ninja:plan-probe-v2"

_RE_ROUTE_BLOCK = re.compile(r"// ninja:(plan-probe|settings-probe)[\s\S]*?\n\}\);\n")


def _strip_probe_routes(content, family):
    """Remove previously generated route blocks of one family only."""
    pattern = re.compile(r"// ninja:" + family + r"[\s\S]*?\n\}\);\n")
    return pattern.sub("", content)


PLAN_PROBE_JS = r"""// ninja:plan-probe-v2 — read the signed-in account's plan from ChatGPT's own web API.
// Runs on any open chatgpt.com page (or a temporary one) and never acquires the
// manual-login lease, so it is safe during/next to generation jobs. Returns only
// billing-relevant fields: never tokens, cookies or chat content.
router.get('/login/plan', async (req, res) => {
  let temp = null;
  try {
    const context = await getContext();
    let page = null;
    for (const candidate of context.pages()) {
      if (candidate && !candidate.isClosed() && (candidate.url() || '').startsWith('https://chatgpt.com')) {
        page = candidate;
        break;
      }
    }
    if (!page) {
      temp = await context.newPage();
      page = temp;
      await page.goto('https://chatgpt.com', { waitUntil: 'domcontentloaded', timeout: 45000 });
    }
    const data = await page.evaluate(async () => {
      const out = {};
      const grab = async (key, url) => {
        try {
          const r = await fetch(url, { credentials: 'include', headers: { accept: 'application/json' } });
          let body = null;
          if (r.status === 200) {
            try { body = await r.json(); } catch (e) { body = null; }
          }
          out[key] = { status: r.status, body };
        } catch (e) {
          out[key] = { error: String((e && e.message) || e) };
        }
      };
      await grab('me', '/backend-api/me');
      await grab('check', '/backend-api/accounts/check/v4-2023-04-27');
      try {
        const r = await fetch('/api/auth/session', { credentials: 'include' });
        const s = r.status === 200 ? await r.json() : null;
        out.session = {
          status: r.status,
          expires: s && s.expires ? s.expires : null,
          user: s && s.user ? { id: s.user.id || null, email: s.user.email || null } : null,
        };
      } catch (e) {
        out.session = { error: String((e && e.message) || e) };
      }
      return out;
    });
    res.json(data);
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  } finally {
    if (temp && !temp.isClosed()) {
      try { await temp.close(); } catch (e) {}
    }
  }
});

module.exports = router;"""

SETTINGS_PROBE_MARKER = "ninja:settings-probe-v2"

SETTINGS_PROBE_JS = r"""// ninja:settings-probe-v2 — read Settings -> Account (plan + workspace id).
router.get('/login/settings', async (req, res) => {
  let temp = null;
  try {
    const context = await getContext();
    temp = await context.newPage();
    await temp.goto('https://chatgpt.com/settings/account', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await temp.waitForTimeout(4500);
    const payload = await temp.evaluate(() => {
      const match = document.cookie.match(/(?:^|;\s*)_account=([^;]+)/);
      return {
        workspaceId: match ? decodeURIComponent(match[1]) : null,
        url: location.href,
        title: document.title,
        text: document.body ? document.body.innerText.slice(0, 8000) : '',
      };
    });
    res.json({ page: payload });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  } finally {
    if (temp && !temp.isClosed()) {
      try { await temp.close(); } catch (e) {}
    }
  }
});

module.exports = router;"""


LIVE_VIEW_MARKER = "ninja:live-view"

LIVE_VIEW_JS = r"""// ninja:live-view — real-time frames, trusted input, hover cursor + click feedback.
// GET  /login/live?mode=poll|screencast&q=fast|balanced|sharp -> multipart MJPEG
// GET  /login/cursor?x=&y= -> what the remote page would do with the pointer there
// POST /login/input -> batched mouse/key/text events via CDP Input domain
const LIVE_PRESETS = {
  fast: { quality: 30, maxWidth: 720, maxHeight: 450, interval: 45, scale: 0.55 },
  balanced: { quality: 38, maxWidth: 860, maxHeight: 560, interval: 55, scale: 0.7 },
  sharp: { quality: 52, maxWidth: 1280, maxHeight: 800, interval: 80, scale: 1 },
};

router.get('/login/live', async (req, res) => {
  let client = null;
  let timer = null;
  let closed = false;
  try {
    await enterManualLogin({ acquire: false });
    const page = await getLoginPage();
    const mode = (req.query && req.query.mode) === 'screencast' ? 'screencast' : 'poll';
    const preset = LIVE_PRESETS[(req.query && req.query.q) || 'fast'] || LIVE_PRESETS.fast;
    res.writeHead(200, {
      'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
      'Cache-Control': 'no-store, no-cache, must-revalidate',
      'Pragma': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    });

    const writeFrame = (buffer) => {
      if (closed) return;
      res.write('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + buffer.length + '\r\n\r\n');
      res.write(buffer);
      res.write('\r\n');
    };

    if (mode === 'screencast') {
      try { await page.bringToFront(); } catch (e) {}
      // Keep the compositor busy with a 2px pulse: screencast only emits on repaint.
      await page.evaluate(() => {
        if (document.getElementById('__ninja_live_pulse__')) return;
        const dot = document.createElement('div');
        dot.id = '__ninja_live_pulse__';
        dot.style.cssText =
          'position:fixed;left:0;top:0;width:2px;height:2px;opacity:.98;pointer-events:none;z-index:2147483647';
        document.documentElement.appendChild(dot);
        const style = document.createElement('style');
        style.textContent =
          '@keyframes __ninja_live_pulse{from{opacity:.96}to{opacity:1}}' +
          '#__ninja_live_pulse__{animation:__ninja_live_pulse 1s linear infinite}';
        document.documentElement.appendChild(style);
      }).catch(() => {});
      client = await page.context().newCDPSession(page);
      client.on('Page.screencastFrame', async (frame) => {
        if (closed) return;
        try {
          writeFrame(Buffer.from(frame.data, 'base64'));
          await client.send('Page.screencastFrameAck', { sessionId: frame.sessionId });
        } catch (err) {}
      });
      await client.send('Page.startScreencast', {
        format: 'jpeg',
        quality: preset.quality,
        maxWidth: preset.maxWidth,
        maxHeight: preset.maxHeight,
        everyNthFrame: 1,
      });
    } else {
      client = await page.context().newCDPSession(page);
      let busy = false;
      timer = setInterval(async () => {
        if (closed || busy) return;
        busy = true;
        try {
          const shot = await client.send('Page.captureScreenshot', {
            format: 'jpeg',
            quality: preset.quality,
            optimizeForSpeed: true,
            fromSurface: true,
            clip: {
              x: 0,
              y: 0,
              width: page.viewportSize() ? page.viewportSize().width : 1280,
              height: page.viewportSize() ? page.viewportSize().height : 800,
              scale: preset.scale,
            },
          });
          writeFrame(Buffer.from(shot.data, 'base64'));
        } catch (err) {}
        busy = false;
      }, preset.interval);
    }

    req.on('close', async () => {
      closed = true;
      if (timer) clearInterval(timer);
      if (client) {
        try { await client.send('Page.stopScreencast'); } catch (err) {}
        try { await client.detach(); } catch (err) {}
      }
      try { res.end(); } catch (e) {}
    });
  } catch (err) {
    if (timer) clearInterval(timer);
    if (client) { try { await client.detach(); } catch (e) {} }
    if (!res.headersSent) res.status(err.status || 500).json({ error: err.message });
    else res.end();
  }
});

router.get('/login/cursor', async (req, res) => {
  try {
    await enterManualLogin({ acquire: false });
    const page = await getLoginPage();
    const x = Number((req.query && req.query.x) || 0);
    const y = Number((req.query && req.query.y) || 0);
    const info = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y);
      if (!el) return { cursor: 'default', tag: null, editable: false };
      const style = window.getComputedStyle(el);
      const tag = (el.tagName || '').toLowerCase();
      const editable = Boolean(
        el.isContentEditable ||
          tag === 'input' ||
          tag === 'textarea' ||
          el.closest('[contenteditable="true"]'),
      );
      const interactive =
        editable ||
        tag === 'button' ||
        tag === 'a' ||
        tag === 'select' ||
        Boolean(el.closest('button, a, [role="button"], [role="menuitem"]'));
      let cursor = style.cursor || 'default';
      if (editable) cursor = 'text';
      else if (interactive && (cursor === 'auto' || cursor === 'default')) cursor = 'pointer';
      return { cursor, tag, editable, interactive };
    }, { x, y });
    res.json(info);
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  }
});

const LIVE_VK = {
  Enter: 13, Tab: 9, Backspace: 8, Escape: 27, ArrowUp: 38, ArrowDown: 40,
  ArrowLeft: 37, ArrowRight: 39, Delete: 46, Home: 36, End: 35,
  PageUp: 33, PageDown: 34, ' ': 32, Shift: 16, Control: 17, Alt: 18, Meta: 91,
};

router.post('/login/input', async (req, res) => {
  let client = null;
  try {
    await enterManualLogin({ acquire: false });
    const page = await getLoginPage();
    try { await page.bringToFront(); } catch (e) {}
    client = await page.context().newCDPSession(page);
    const events = Array.isArray(req.body && req.body.events) ? req.body.events.slice(0, 200) : [];
    for (const ev of events) {
      if (!ev || typeof ev !== 'object') continue;
      if (ev.type === 'mouse') {
        await client.send('Input.dispatchMouseEvent', {
          type: ev.action === 'down' ? 'mousePressed' : ev.action === 'up' ? 'mouseReleased' : ev.action === 'wheel' ? 'mouseWheel' : 'mouseMoved',
          x: Number(ev.x) || 0,
          y: Number(ev.y) || 0,
          button: ev.button === 'right' ? 'right' : 'left',
          buttons: Number(ev.buttons) || 0,
          clickCount: 1,
          deltaX: Number(ev.deltaX) || 0,
          deltaY: Number(ev.deltaY) || 0,
        });
      } else if (ev.type === 'key') {
        const vk = Number(ev.vk) || LIVE_VK[ev.key] || 0;
        const key = String(ev.key || '');
        await client.send('Input.dispatchKeyEvent', {
          type: 'rawKeyDown', key, code: key,
          windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
        });
        await client.send('Input.dispatchKeyEvent', {
          type: 'keyUp', key, code: key,
          windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
        });
      } else if (ev.type === 'text') {
        const chunk = String(ev.text || '').slice(0, 500);
        if (!chunk) continue;
        await client.send('Input.insertText', { text: chunk });
        const last = chunk[chunk.length - 1];
        await client.send('Input.dispatchKeyEvent', {
          type: 'keyDown', key: last, text: last, unmodifiedText: last,
        });
        await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: last });
      }
    }
    res.json({ ok: true, applied: events.length });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  } finally {
    if (client) { try { await client.detach(); } catch (e) {} }
  }
});

module.exports = router;"""


def _patch_live_view(worker):
    """Install the live-view routes once; both routes belong to the same block."""
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if LIVE_VIEW_MARKER in content:
        # v3 marker: cursors + quality presets. Older blocks get upgraded once.
        if "/login/cursor" in content and "LIVE_PRESETS" in content:
            return False
        start = content.index("// ninja:live-view")
        end = content.index("module.exports = router;", start)
        content = (
            content[:start]
            + "module.exports = router;"
            + content[end + len("module.exports = router;") :]
        )
    anchor = "module.exports = router;"
    if content.count(anchor) != 1:
        return False
    path.write_text(content.replace(anchor, LIVE_VIEW_JS, 1), encoding="utf-8")
    return True


def _install_probe(worker, js, marker, family):
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    stripped = _strip_probe_routes(content, family)
    if marker in stripped and stripped == content:
        return False
    anchor = "module.exports = router;"
    if stripped.count(anchor) != 1:
        return False
    path.write_text(stripped.replace(anchor, js, 1), encoding="utf-8")
    return True


def _patch_plan_probe(worker):
    return _install_probe(worker, PLAN_PROBE_JS, PLAN_PROBE_MARKER, "plan-probe")


def _patch_settings_probe(worker):
    return _install_probe(worker, SETTINGS_PROBE_JS, SETTINGS_PROBE_MARKER, "settings-probe")


OCCLUSION_MARKER = "ninja:no-occlusion-throttle"


def _patch_launch_args(worker):
    """Disable Chrome's occlusion/renderer backgrounding for live streaming + timers."""
    path = pathlib.Path(worker) / "src" / "browser-fingerprint.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if OCCLUSION_MARKER in content:
        return False
    old = (
        "function launchArgs(engine = 'stealth') {\n"
        "  return engine === 'patchright'\n"
        "    ? COMMON_CHROMIUM_ARGS.filter((a) => !a.startsWith('--disable-blink-features='))\n"
        "    : [...COMMON_CHROMIUM_ARGS];\n"
        "}"
    )
    if content.count(old) != 1:
        return False
    new = (
        "// ninja:no-occlusion-throttle — keep painting and timers alive when the window\n"
        "// is covered or minimized, so the live screencast stays smooth and automation\n"
        "// timers do not get backgrounded.\n"
        "const NINJA_LIVE_ARGS = [\n"
        "  '--disable-backgrounding-occluded-windows',\n"
        "  '--disable-renderer-backgrounding',\n"
        "  '--disable-background-timer-throttling',\n"
        "  '--disable-features=CalculateNativeWinOcclusion',\n"
        "];\n"
        "\n"
        "function launchArgs(engine = 'stealth') {\n"
        "  const base = engine === 'patchright'\n"
        "    ? COMMON_CHROMIUM_ARGS.filter((a) => !a.startsWith('--disable-blink-features='))\n"
        "    : [...COMMON_CHROMIUM_ARGS];\n"
        "  return [\n"
        "    ...base.filter((arg) => !NINJA_LIVE_ARGS.includes(arg)),\n"
        "    ...NINJA_LIVE_ARGS,\n"
        "  ];\n"
        "}"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


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
    _patch_popup_follow(worker)
    _patch_project_url(worker)
    _patch_login_diagnostics(worker)
    _patch_plan_probe(worker)
    _patch_settings_probe(worker)
    _patch_live_view(worker)
    _patch_launch_args(worker)
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


def _kill_tree(pid, timeout=6):
    """Terminate a process and its children (Chrome is a child of the Node gateway)."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        return
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        parent.terminate()
    except psutil.Error:
        pass
    done, alive = psutil.wait_procs([parent] + children, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass


def reclaim_orphan_worker(ctx, acc):
    """Kill a gateway left behind by a hard-killed panel so the port can be rebound.

    A previous process that died without its shutdown hook (taskkill /F, OOM, container
    stop) leaves `node src/server.js` and its Chrome children running. They hold the
    account port, so the next start is refused forever. Only processes that provably
    belong to this account's workspace are touched.
    """
    if ctx.port_available(acc["port"]):
        return False
    worker_dir = str(ctx.DATA / "accounts" / acc["id"] / "gateway")
    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name.startswith("node"):
                continue
            command = " ".join(proc.info.get("cmdline") or [])
            if "src/server.js" not in command:
                continue
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = ""
            if worker_dir not in command and cwd != worker_dir:
                continue
            _kill_tree(proc.pid)
            killed += 1
        except psutil.Error:
            continue
    if not killed:
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ctx.port_available(acc["port"]):
            return True
        time.sleep(0.25)
    return ctx.port_available(acc["port"])


def touch_account(ctx, account_id):
    """Record real use of an account's browser, so the idle reaper leaves it alone."""
    ctx.account_activity[account_id] = time.monotonic()


def wake_worker_async(ctx, acc):
    """Start a worker on demand (queued job, login page) without fighting the operator.

    Deliberately separate from the crash supervisor: an operator Stop is respected unless
    the account is only idle-reaped, and repeated failed wake attempts back off so a broken
    install cannot spawn a process every two seconds.
    """
    aid = acc["id"]
    now = time.monotonic()
    state = ctx.wake_state.get(aid)
    if state and now < state["next"]:
        return False
    attempts = (state["attempts"] if state else 0) + 1
    # Back off (20s, 40s, ... up to 5 min) so a broken install cannot spawn a process every
    # scheduler tick. After many failures it keeps a slow retry instead of giving up.
    delay = min(20 * (2 ** (attempts - 1)), 300)
    ctx.wake_state[aid] = {"attempts": attempts, "next": now + delay}
    ctx.idle_stopped.discard(aid)
    ctx.suppressed_workers.discard(aid)
    ctx.worker_restart_state.pop(aid, None)
    touch_account(ctx, aid)
    ctx.start_worker_async(acc, manual=False)
    return True


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


def start_worker(ctx, acc, manual=True):
    with ctx.proc_guard:
        latest = ctx.query_account(acc["id"])
        if not latest or latest["archived_at"]:
            raise RuntimeError("Account archived or removed; worker start cancelled.")
        if manual:
            # An explicit Start clears the operator stop and re-arms crash supervision.
            ctx.suppressed_workers.discard(acc["id"])
            ctx.worker_restart_state.pop(acc["id"], None)
        elif acc["id"] in ctx.suppressed_workers:
            # A stop landed while this automatic start was queued; never override it.
            return
        existing = ctx.processes.get(acc["id"])
        if existing and existing.poll() is None:
            return
        if not ctx.port_available(acc["port"]):
            # A hard-killed panel leaves its gateway behind holding this port. Reclaim only
            # a process that provably belongs to this account, then retry once.
            if not ctx.reclaim_orphan_worker(acc):
                raise RuntimeError(
                    "Worker port is occupied by a process that does not belong to this "
                    f"account (port {acc['port']}); free it and start again."
                )
        running = sum((1 for worker in ctx.processes.values() if worker.poll() is None))
        cap = ctx.setting("max_running_accounts")
        if running >= cap:
            raise RuntimeError(
                f"Safety limit: at most {cap} browser workers may run. Stop another worker or increase MAX_RUNNING_ACCOUNTS after checking available RAM."
            )
        # RAM guard: with 10 accounts a single OOM kill takes down every worker and the
        # dashboard with it. Refuse the start instead of letting the kernel decide.
        guard_mb = int(ctx.setting("min_free_memory_mb") or 0)
        if guard_mb:
            available_mb = psutil.virtual_memory().available // (1024 * 1024)
            if available_mb < guard_mb:
                raise RuntimeError(
                    f"Not enough free RAM to start another browser: {available_mb} MB "
                    f"available, min_free_memory_mb={guard_mb}. Stop an idle account, lower "
                    "max_running_accounts, or lower idle_worker_shutdown_minutes."
                )
        if display_mode() == "missing":
            raise RuntimeError(
                "Linux browser display unavailable: install xvfb (apt install xvfb) so "
                "each worker gets its own virtual display, or run under a real DISPLAY."
            )
        worker = ctx.ensure_worker_copy(acc)
        env = worker_env(ctx, acc)
        cmd = worker_command()
        log_path = ctx.DATA / "accounts" / acc["id"] / "worker.log"
        h = open(log_path, "ab", buffering=0)
        try:
            p = subprocess.Popen(
                cmd,
                cwd=str(worker),
                env=env,
                stdout=h,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
                start_new_session=os.name != "nt",
            )
        except Exception:
            h.close()
            raise
        ctx.processes[acc["id"]] = p
        ctx.log_handles[acc["id"]] = h
        ctx.worker_launch_settings[acc["id"]] = (
            ctx.setting("gateway_max_tabs"),
            ctx.setting("max_batch_images"),
        )
        ctx.idle_stopped.discard(acc["id"])
        ctx.wake_state.pop(acc["id"], None)
        touch_account(ctx, acc["id"])


def stop_worker(ctx, account_id):
    with ctx.proc_guard:
        # Remember the intentional stop so the crash supervisor does not immediately
        # bring this worker back.
        ctx.suppressed_workers.add(account_id)
        p = ctx.processes.pop(account_id, None)
        ctx.worker_restart_state.pop(account_id, None)
        ctx.worker_launch_settings.pop(account_id, None)
        h = ctx.log_handles.pop(account_id, None)
        if p and p.poll() is None:
            if os.name != "nt":
                import signal

                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                # Terminating Node alone orphans its Chrome children and leaves them
                # holding RAM/display. Kill the whole tree.
                try:
                    children = psutil.Process(p.pid).children(recursive=True)
                except psutil.Error:
                    children = []
                for child in children:
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                try:
                    p.terminate()
                except OSError:
                    pass
            try:
                p.wait(timeout=6)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal

                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    p.kill()
                p.wait(timeout=3)
            if os.name == "nt":
                psutil.wait_procs(children, timeout=3)
        if h:
            h.close()


def focus_worker_window(ctx, acc):
    """Bring the worker's Chrome window to the foreground (Windows only).

    A minimized/occluded window makes captureScreenshot slow and CDP screencast
    return black frames. Linux/Xvfb has no such state, so this is a no-op there.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    import ctypes.wintypes as wt

    with ctx.proc_guard:
        process = ctx.processes.get(acc["id"])
    if not process or process.poll() is not None:
        return False
    try:
        pids = {process.pid}
        pids.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
    except psutil.Error:
        return False
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return False
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    restored = False
    for hwnd in found:
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            restored = True
        except Exception:
            continue
    return restored


def _identity_after_start(ctx, acc):
    """Give the worker a moment, then read who actually signed in."""
    try:
        for _ in range(120):
            if ctx.process_state(acc) != "running":
                return
            if ctx.upstream_probe(acc):
                break
            time.sleep(0.5)
        plan = plan_snapshot(ctx, acc)
        if plan:
            ctx.save_detected_plan(acc["id"], plan)
    except Exception:
        pass


def start_worker_async(ctx, acc, manual=False):

    def start():
        try:
            ctx.start_worker(acc, manual=manual)
            threading.Thread(
                target=_identity_after_start, args=(ctx, acc), daemon=True
            ).start()
        except Exception as e:
            (ctx.DATA / "accounts" / acc["id"]).mkdir(parents=True, exist_ok=True)
            with (ctx.DATA / "accounts" / acc["id"] / "worker.log").open(
                "a", encoding="utf8"
            ) as f:
                f.write(f"\n[manager] startup failed: {e}\n")

    threading.Thread(target=start, daemon=True, name="worker-start").start()
