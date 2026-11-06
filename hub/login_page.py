"""The remote-login page.

Kept as a plain template (not an f-string) so the browser-side JavaScript is readable and
easy to audit. Every state the operator can hit is handled on the page itself: the worker
waking up, a generation job holding the browser, the gateway's 10-minute manual-login
lease expiring, a panel session expiring, and the post-save state where the upstream login
page is deliberately closed.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect __TITLE__ · Ninja Image Hub</title>
<style>
  :root { --bg:#080f1e; --panel:#0f1b2d; --line:#2b4160; --text:#e8eefb; --muted:#9caccc; --accent:#b9f85c; --good:#68dfbe; --bad:#ff8080; }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; margin: 0; padding: 22px; }
  main { max-width: 1280px; margin: auto; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
  h2 { margin: 0 0 4px; } p { color: var(--muted); margin: 4px 0; }
  button { border: 1px solid #3b506f; background: #172940; color: #fff; border-radius: 9px; padding: 10px 14px; cursor: pointer; font: inherit; }
  button:hover:not(:disabled) { background: #284267; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.primary { background: var(--accent); color: #0a1220; border-color: var(--accent); font-weight: 600; }
  input { flex: 1; min-width: 220px; background: #111f32; border: 1px solid var(--line); color: #fff; border-radius: 9px; padding: 11px 12px; font: inherit; }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; align-items: center; }
  .notice { padding: 12px 14px; background: #152137; border: 1px solid #2c496f; border-radius: 10px; color: var(--muted); font-size: 13px; }
  #state { margin: 12px 0; padding: 10px 12px; border-radius: 9px; border: 1px solid var(--line); background: #0c1727; color: var(--muted); font-size: 14px; }
  #state.good { border-color: #2f6f57; color: var(--good); }
  #state.bad { border-color: #6f3030; color: var(--bad); }
  .stage { position: relative; display: inline-block; max-width: 100%; line-height: 0; }
  .screen { display: block; width: 100%; max-width: 1280px; border: 1px solid var(--line); border-radius: 14px; background: #15243a; cursor: crosshair; min-height: 120px; user-select: none; -webkit-user-drag: none; touch-action: none; }
  #marker { position: absolute; width: 20px; height: 20px; margin: -10px 0 0 -10px; border: 2px solid var(--accent); border-radius: 50%; box-shadow: 0 0 0 2px rgba(0,0,0,.5); pointer-events: none; opacity: 0; transition: opacity .25s ease; }
  #marker.show { opacity: 1; }
  .screen:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
  .hint { color: var(--muted); font-size: 12px; line-height: 1.5; }
  .hint b { color: var(--text); }
</style>
</head>
<body>
<main>
  <header>
    <div><h2>Connect: __TITLE__</h2><p>Private browser session · manual login · session saved locally</p></div>
    <button onclick="window.close()">Close</button>
  </header>
  <p class="notice">Only sign in to an account you own. This page drives the worker browser; the panel does not store these credentials. Do not expose this page beyond your own machine or SSH tunnel.</p>
  <p class="notice">__DISPLAY_NOTE__</p>
  <p class="notice">Signing in with <b>Google</b>, <b>Apple</b> or <b>phone</b> opens a popup: this page follows the newest window automatically. If the image does not change within ~10 seconds, use the visible Chrome window (desktop) or VNC (server) for that provider.</p>
  <div class="toolbar">
    <button class="primary" id="openBtn" onclick="reopen()">Open ChatGPT</button>
    <button onclick="sendKey('Tab')">Tab</button>
    <button onclick="sendKey('Enter')">Enter</button>
    <button onclick="sendKey('Escape')">Esc</button>
    <button id="saveBtn" onclick="saveSession()">Save Session</button>
    <button onclick="manualRefresh()">Refresh</button>
    <button onclick="sendKey('PageUp')">PgUp</button>
    <button onclick="sendKey('PageDown')">PgDn</button>
    <button id="directBtn" onclick="toggleDirect()" style="background:var(--accent);color:#0a1220">Type directly: on</button>
  </div>
  <div class="toolbar">
    <input id="type" type="password" placeholder="Type into the focused browser field" autocomplete="off">
    <button onclick="typeNow()">Type</button>
    <button onclick="toggleShow()">Show / Hide</button>
  </div>
  <div id="state">Starting…</div>
  <div class="stage" id="stage">
    <img id="screen" class="screen" tabindex="0" alt="Live browser screenshot" draggable="false" onclick="clickScreen(event)" ondblclick="doubleClick(event)" onwheel="wheelScroll(event)">
    <div id="marker" aria-hidden="true"></div>
  </div>
  <p class="hint" id="footnote"><b>How to interact:</b> there is no live mouse cursor inside the image — every click is forwarded to the worker and a green ring shows where it landed. Click the field (the ring appears), then type below and press <b>Enter</b>, or turn on <b>type directly</b> to type without leaving the image. The wheel scrolls the page.</p>
</main>
<script>
const BASE = __BASE__;
const CSRF = __CSRF__;
const SAVE_URL = BASE + '/save';
let busy = false;
let saved = false;
let polling = false;
let timer = null;
let lastOk = 0;
let pollStartedAt = 0;
let failures = 0;
let lastActionAt = 0;
const MAX_POLL_MS = 9 * 60 * 1000;   // stop before the gateway's 10-min manual-login lease

const stateBox = document.getElementById('state');
const screenImg = document.getElementById('screen');
const saveBtn = document.getElementById('saveBtn');
const openBtn = document.getElementById('openBtn');

function setState(text, kind) {
  stateBox.textContent = text;
  stateBox.className = kind === 'good' ? 'good' : kind === 'bad' ? 'bad' : '';
}

function elapsed() { return Date.now() - pollStartedAt; }

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function getJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new HttpError(res.status, data.detail || data.error || ('HTTP ' + res.status));
  return data;
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-csrf-token': CSRF },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new HttpError(res.status, data.detail || data.error || ('HTTP ' + res.status));
  return data;
}

function sessionExpired() {
  stopPolling();
  setState('Panel session expired. Reload this page and sign in to the panel again.', 'bad');
}

async function openSession() {
  setState('Waking the worker browser…');
  try {
    await getJson(BASE + '/init');
    setState('Browser session ready. Waiting for the first screenshot…', 'good');
    return 'ok';
  } catch (err) {
    if (err.status === 403) { sessionExpired(); return 'fatal'; }
    if (err.status === 409 || err.status === 503) {
      const fatal = /not installed|did not start within/i.test(err.message);
      setState(err.message + (fatal ? '' : ' Retrying automatically…'));
      return fatal ? 'fatal' : 'retry';
    }
    setState(err.message, 'bad');
    return 'fatal';
  }
}

async function bootstrap() {
  setState('Opening the login session…');
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const result = await openSession();
    if (result === 'ok') { await refreshOnce(); startPolling(); if (directMode) screenImg.focus(); return; }
    if (result === 'fatal') return;
    await new Promise((r) => setTimeout(r, Math.min(1500 * (attempt + 1), 5000)));
  }
  setState('The browser did not open the login session. Check Accounts → Logs and run scripts/doctor.py, then press Open ChatGPT.', 'bad');
}

async function refreshOnce() {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch(BASE + '/screenshot?ts=' + Date.now(), { cache: 'no-store' });
    if (res.status === 403) { sessionExpired(); return; }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new HttpError(res.status, data.detail || data.error || 'Worker unavailable');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const old = screenImg.dataset.url;
    screenImg.dataset.url = url;
    screenImg.src = url;
    if (old) setTimeout(() => URL.revokeObjectURL(old), 2000);
    lastOk = Date.now();
    failures = 0;
    if (!saved && Date.now() - lastActionAt > 6000) setState('Live. Click a field in the image, then just type (or paste) — keys go to the browser.', 'good');
  } catch (err) {
    if (err.status === 403) { sessionExpired(); return; }
    failures += 1;
    if (!saved && failures <= 3) {
      setState('Login session paused — reopening it…');
      const recovered = await openSession();
      if (recovered === 'ok') { setTimeout(() => refreshOnce().catch(() => {}), 600); return; }
    }
    setState(err.message + (err.status === 409 ? '' : ' Retrying…'), 'bad');
  } finally {
    busy = false;
  }
}

function startPolling() {
  if (polling) return;
  polling = true;
  pollStartedAt = Date.now();
  timer = setInterval(() => {
    if (saved) return stopPolling();
    if (document.hidden) return;
    if (elapsed() > MAX_POLL_MS) {
      stopPolling();
      setState('Polling paused so queued jobs can use the browser. Press Open ChatGPT to resume.', 'bad');
      return;
    }
    refreshOnce().catch(() => {});
  }, 4000);
}

function stopPolling() {
  polling = false;
  if (timer) { clearInterval(timer); timer = null; }
}

async function manualRefresh() {
  if (!polling) { await bootstrap(); return; }
  await refreshOnce();
}

async function reopen() {
  saved = false;
  saveBtn.disabled = false;
  stopPolling();
  await bootstrap();
}

async function sendAction(path, body) {
  if (busy) return;
  busy = true;
  try {
    await postJson(BASE + '/' + path, body);
    setTimeout(() => refreshOnce().catch(() => {}), 600);
    return true;
  } catch (err) {
    if (err.status === 403) { sessionExpired(); return false; }
    if (err.status === 409) {
      setState('Login session paused — reopening it…');
      if ((await openSession()) === 'ok') {
        try {
          await postJson(BASE + '/' + path, body);
          setTimeout(() => refreshOnce().catch(() => {}), 600);
          return true;
        } catch (retryErr) { setState(retryErr.message, 'bad'); return false; }
      }
      return false;
    }
    setState(err.message, 'bad');
    return false;
  } finally {
    busy = false;
  }
}

function navigate() { sendAction('navigate', { url: 'https://chatgpt.com' }); }
function sendKey(key) { sendAction('key', { key }); }

function toggleShow() {
  const el = document.getElementById('type');
  el.type = el.type === 'password' ? 'text' : 'password';
}

function typeNow() {
  const el = document.getElementById('type');
  const value = el.value;
  if (!value) return;
  el.value = '';
  lastActionAt = Date.now();
  setState('Typed ' + value.length + ' character(s) into the focused browser field.');
  sendAction('type', { text: value }).then(() => el.focus());
}

document.getElementById('type').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); typeNow(); }
});

const marker = document.getElementById('marker');
const typeInput = document.getElementById('type');
const directBtn = document.getElementById('directBtn');
const stage = document.getElementById('stage');
let directMode = true;
let markerTimer = null;
let scrollCooldown = 0;
let keyBuffer = '';
let keyTimer = null;

function showMarker(clientX, clientY) {
  const rect = screenImg.getBoundingClientRect();
  marker.style.left = (clientX - rect.left) + 'px';
  marker.style.top = (clientY - rect.top) + 'px';
  marker.classList.add('show');
  if (markerTimer) clearTimeout(markerTimer);
  markerTimer = setTimeout(() => marker.classList.remove('show'), 2600);
}

function toRemote(event) {
  const width = screenImg.naturalWidth || 1280;
  const height = screenImg.naturalHeight || 720;
  const rect = screenImg.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: Math.max(0, Math.min(width - 1, Math.round((event.clientX - rect.left) * width / rect.width))),
    y: Math.max(0, Math.min(height - 1, Math.round((event.clientY - rect.top) * height / rect.height))),
  };
}

function clickScreen(event) {
  const point = toRemote(event);
  if (!point) return;
  showMarker(event.clientX, event.clientY);
  lastActionAt = Date.now();
  setState('Clicked at ' + point.x + ',' + point.y + ' — green ring shows where it landed.');
  if (directMode) screenImg.focus(); else typeInput.focus();
  sendAction('click', point);
}

function doubleClick(event) {
  const point = toRemote(event);
  if (!point) return;
  showMarker(event.clientX, event.clientY);
  lastActionAt = Date.now();
  setState('Double-click at ' + point.x + ',' + point.y);
  sendAction('click', point).then(() => sendAction('click', point));
}

function wheelScroll(event) {
  event.preventDefault();
  const now = Date.now();
  if (now < scrollCooldown) return;
  scrollCooldown = now + 220;
  const key = event.deltaY > 0 ? 'PageDown' : 'PageUp';
  sendKey(key);
  setState('Scrolled (' + key + ')');
}

function flushKeys() {
  if (!keyBuffer) return;
  const text = keyBuffer;
  keyBuffer = '';
  lastActionAt = Date.now();
  setState('Typed ' + text.length + ' character(s) directly into the browser.');
  sendAction('type', { text }).then(() => { if (directMode) screenImg.focus(); });
}

function toggleDirect() {
  directMode = !directMode;
  directBtn.textContent = 'Type directly: ' + (directMode ? 'on' : 'off');
  directBtn.style.background = directMode ? 'var(--accent)' : '';
  directBtn.style.color = directMode ? '#0a1220' : '#fff';
  setState(directMode
    ? 'Direct typing is ON — click the browser field, then type or paste anywhere. Enter/Tab/Backspace work.'
    : 'Direct typing is OFF — type in the box and press Enter.');
  if (directMode) screenImg.focus();
}

document.addEventListener('paste', (event) => {
  if (!directMode) return;
  const text = (event.clipboardData || window.clipboardData).getData('text');
  if (!text) return;
  event.preventDefault();
  lastActionAt = Date.now();
  setState('Pasted ' + text.length + ' character(s) into the browser.');
  sendAction('type', { text });
});

document.addEventListener('keydown', (event) => {
  if (!directMode) return;
  if (event.target === typeInput) return; // the normal input handler owns this
  if (/^(INPUT|TEXTAREA|SELECT)$/.test((event.target.tagName || '').toUpperCase())) return;
  const special = ['Enter', 'Tab', 'Escape', 'Backspace', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
  if (special.includes(event.key)) {
    event.preventDefault();
    flushKeys();
    sendKey(event.key);
    return;
  }
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    keyBuffer += event.key;
    if (keyTimer) clearTimeout(keyTimer);
    keyTimer = setTimeout(flushKeys, 140);
  }
});

async function saveSession() {
  if (busy) return;
  busy = true;
  saveBtn.disabled = true;
  setState('Saving the session…');
  try {
    let data;
    try {
      data = await postJson(SAVE_URL, {});
    } catch (firstErr) {
      if (firstErr.status !== 409) throw firstErr;
      // The gateway's manual-login lease can expire while the tab sits open: reopen it.
      setState('Login session expired — reopening, then saving again…');
      if ((await openSession()) !== 'ok') throw firstErr;
      data = await postJson(SAVE_URL, {});
    }
    saved = true;
    stopPolling();
    if (data.session === 'connected') {
      setState('Saved ✓ — the gateway confirms the session is connected. Close this page and send a job.', 'good');
    } else if (data.session === 'unverified') {
      setState('Saved ✓ — cookies are stored, but the gateway has not confirmed the session yet. Send a test job; if it fails, reopen this page and sign in again.', 'good');
    } else {
      setState('Saved ✓ — session state is unknown right now. Close this page and send a test job.', 'good');
    }
    if (data.blocker) {
      stateBox.textContent += ' Gateway login blocker: ' + data.blocker + '.';
    }
  } catch (err) {
    if (err.status === 403) sessionExpired();
    else setState(err.message + ' Press Open ChatGPT, sign in again, then Save.', 'bad');
    saveBtn.disabled = false;
  } finally {
    busy = false;
  }
}

document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshOnce().catch(() => {}); });
bootstrap();
</script>
</body>
</html>
"""


def render(account_id, title, csrf_js, display_note):
    return (
        PAGE.replace("__TITLE__", title)
        .replace("__CSRF__", csrf_js)
        .replace("__BASE__", '"/api/accounts/%s/remote"' % account_id)
        .replace("__DISPLAY_NOTE__", display_note)
    )
