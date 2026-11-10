"""Runtime patches applied to the vendored gateway workspace.

The vendored gateway is pinned but its Playwright selectors are not an API; every patch is a
literal anchor replacement and must stay idempotent and fail-open (an upstream change keeps
the worker usable, just without the optimisation). Keeping them in one registry lets the
doctor verify the same markers that the worker applies.
"""

import os
import pathlib
import re
import sys

PATCH_ORDER = (
    "_patch_popup_follow",
    "_patch_project_url",
    "_patch_login_diagnostics",
    "_patch_plan_probe",
    "_patch_settings_probe",
    "_patch_live_view",
    "_patch_launch_args",
    "_patch_image_failure_detect",
    "_patch_fast_prompt_insert",
)

PATCH_MARKERS = {
    "popup_follow": "ninja:popup-follow",
    "fast_prompt_insert": "ninja:fast-prompt-insert",
    "project_url": "ninja:project-url",
    "login_diagnostics": "ninja:pages-routes",
    "plan_probe": "ninja:plan-probe",
    "settings_probe": "ninja:settings-probe",
    "live_view": "ninja:live-view",
    "launch_args": "ninja:launch-args",
    "image_failure": "ninja:image-failure-detect",
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



FAST_INSERT_MARKER = "ninja:fast-prompt-insert"


def _patch_fast_prompt_insert(worker):
    """Insert the prompt in one CDP call when attachments must be preserved.

    `fill()` would wipe ChatGPT attachments, so upstream falls back to per-key typing at
    TYPE_DELAY_MS plus CDP round-trip overhead. Measured on this host: a 3,126-character
    prompt took 95.5 s to type and dominated the whole job (144.9 s gateway time).
    `keyboard.insertText` dispatches the same input events in one call and keeps the
    composer's attachment chips; if the text does not land the upstream verification path
    still falls back to per-key typing, so this patch is fail-open.
    """
    path = pathlib.Path(worker) / "src" / "chatgpt.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if FAST_INSERT_MARKER in content:
        return False
    old = (
        "      typedPerKey = true;\n"
        "      await typeLines(text,\n"
        "        (line) => p.keyboard.type(line, { delay: TYPE_DELAY_MS }),\n"
        "        () => p.keyboard.press('Shift+Enter'));"
    )
    if content.count(old) != 1:
        return False
    new = (
        "      typedPerKey = true;\n"
        "      // ninja:fast-prompt-insert — keep attachments, skip the per-key delay.\n"
        "      const __insertStart = Date.now();\n"
        "      await typeLines(text,\n"
        "        (line) => p.keyboard.insertText(line),\n"
        "        () => p.keyboard.press('Shift+Enter'));\n"
        "      console.log(`[fast-insert] ${text.length} chars in ${Date.now() - __insertStart} ms`);"
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


IMAGE_FAILURE_MARKER = "ninja:image-failure-detect"


def _patch_image_failure_detect(worker):
    """Treat ChatGPT's 'Image generation failed' card as a failure, not a success.

    The failed turn can still render a large <img>, so the plain image count in
    imageOutcomePredicate would report success and a stale/failed image could be
    delivered. Detection is textual, scoped to the newest assistant turn and
    guarded/idempotent; an upstream change simply skips the patch.
    """
    chatgpt = pathlib.Path(worker) / "src" / "chatgpt.js"
    images = pathlib.Path(worker) / "src" / "routes" / "images.js"
    if not chatgpt.is_file() or not images.is_file():
        return False
    content = chatgpt.read_text(encoding="utf-8")
    if IMAGE_FAILURE_MARKER in content:
        return False

    success_anchor = "  // --- Success -------------------------------------------------------------------------"
    failure_probe = (
        "  // ninja:image-failure-detect — provider-side failure must beat the image count: "
        "the failed card\n"
        "  // can contain a large <img>, and a stale image must never look like success.\n"
        "  if (tailChanged) {\n"
        "    const failedPhrases = ['image generation failed', 'generation failed', "
        "'не удалось создать изображение', 'не удалось сгенерировать изображение'];\n"
        "    if (failedPhrases.some((phrase) => isNew(phrase))) return 'generation_failed';\n"
        "  }\n\n"
    )
    if content.count(success_anchor) != 1:
        return False
    content = content.replace(success_anchor, failure_probe + success_anchor, 1)

    # readProgressIndicators: detect the same failure during adaptive waits.
    progress_anchor = "    const refusal = /i'm not able to|i am not able to|i can't|i cannot|unable to (generate|create|edit|help)|can't help|cannot help|i won't|i will not|не могу|не получится|не в состоянии/i;"
    if content.count(progress_anchor) != 1:
        return False
    content = content.replace(
        progress_anchor,
        progress_anchor
        + "\n    const generationFailed = /image generation failed|generation failed|"
        "не удалось (создать|сгенерировать) изображение/i;",
        1,
    )
    content = content.replace(
        "      refusalLikely: false,\n      activeGeneration: stopBtn || activeText,",
        "      refusalLikely: false,\n      generationFailed: isNewMatch(generationFailed),\n      activeGeneration: stopBtn || activeText,",
        1,
    )

    adaptive_anchor = "            if (progress.refusalLikely) {"
    if content.count(adaptive_anchor) != 1:
        return False
    content = content.replace(
        adaptive_anchor,
        "            if (progress.generationFailed) {\n"
        "              clearInterval(waitInterval);\n"
        "              await saveDebugSnapshot(p, 'generation_failed').catch(() => {});\n"
        "              throw buildError('image_generation_failed', "
        "'ChatGPT reported \"Image generation failed\" — no image was produced', "
        "await readLastAssistantMessage(p));\n"
        "            }\n"
        + adaptive_anchor,
        1,
    )

    # Result handling (non-success outcome map).
    content = content.replace(
        "      refused: 'ChatGPT refused to generate this image',",
        '''      refused: 'ChatGPT refused to generate this image',
      generation_failed:
        'ChatGPT reported "Image generation failed" — no usable image was produced. Open the chat and press Try again, or retry the job.',''',
        1,
    )
    content = content.replace(
        "    if (result_type === 'policy' || result_type === 'refused') {",
        "    if (result_type === 'policy' || result_type === 'refused' || result_type === 'generation_failed') {",
        1,
    )
    content = content.replace(
        "    const codeMap = { policy: 'policy_violation', refused: 'refused', limit: limitKind };",
        "    const codeMap = { policy: 'policy_violation', refused: 'refused', limit: limitKind, generation_failed: 'image_generation_failed' };",
        1,
    )
    content = content.replace(
        "      refused: tail || '',",
        "      refused: tail || '',\n      generation_failed: tail || '',",
        1,
    )

    images_content = images.read_text(encoding="utf-8")
    images_content = images_content.replace(
        "  const statusByKind = {\n    refused: 422,",
        "  const statusByKind = {\n    refused: 422,\n"
        "    // Definitive provider-side failure: the image was not produced. 422 means\n"
        "    // 'do not auto-retry' for shipped clients, which is what we want.\n"
        "    image_generation_failed: 422,",
        1,
    )
    chatgpt.write_text(content, encoding="utf-8")
    images.write_text(images_content, encoding="utf-8")
    return True


def apply_worker_patches(worker):
    """Apply every patch to ``worker``; never raises, returns the applied names."""
    applied = []
    for name in PATCH_ORDER:
        function = globals()[name]
        try:
            if function(worker):
                applied.append(name)
        except Exception as exc:  # noqa: BLE001 - a patch must never break the worker
            print(f"[patches] {name} failed (ignored): {exc}", file=sys.stderr)
    return applied
