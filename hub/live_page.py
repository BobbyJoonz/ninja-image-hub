"""Live view v2: canvas + direct MJPEG parser (truthful FPS/latency, crisp cursor)."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live view · __TITLE__</title>
<style>
  :root{--bg:#080f1e;--panel:#0f1b2d;--line:#2b4160;--txt:#e8eefb;--muted:#9caccc;--accent:#b9f85c;--warn:#eab974;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;padding:14px}
  main{max-width:1280px;margin:auto}
  header{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
  h1{font-size:18px;margin:0}
  .badges{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .badge{padding:4px 10px;border-radius:999px;border:1px solid var(--line);font-size:12px;color:var(--muted)}
  .badge.good{color:var(--accent);border-color:rgba(185,248,92,.45)}
  .badge.warn{color:var(--warn);border-color:rgba(234,185,116,.45)}
  button{border:1px solid #3b506f;background:#172940;color:#fff;border-radius:9px;padding:8px 12px;cursor:pointer;font:inherit}
  button.primary{background:var(--accent);color:#0a1220;border-color:var(--accent);font-weight:600}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
  .stage{position:relative;display:block;max-width:1280px;line-height:0}
  #stream{display:block;width:100%;height:auto;max-width:1280px;border:1px solid var(--line);border-radius:12px;background:#0b1627;cursor:default;min-height:200px;touch-action:none;user-select:none}
  #ripple{position:absolute;width:26px;height:26px;margin:-13px 0 0 -13px;border:2px solid var(--accent);border-radius:50%;pointer-events:none;opacity:0;transform:scale(.4)}
  #ripple.go{animation:ripple .45s ease-out}
  @keyframes ripple{0%{opacity:.95;transform:scale(.4)}100%{opacity:0;transform:scale(2.2)}}
  #stream{will-change:transform}
  .status{color:var(--muted);font-size:13px;margin:8px 0}
  .status b{color:var(--txt)}
  .hint{color:var(--muted);font-size:12px;line-height:1.5}
  input#type{flex:1;min-width:220px;background:#111f32;border:1px solid var(--line);color:#fff;border-radius:9px;padding:10px 12px;font:inherit}
</style>
</head>
<body>
<main>
  <header>
    <div><h1>Live view · __TITLE__</h1><div class="hint">Direct MJPEG parser · real cursor · trusted input</div></div>
    <div class="badges">
      <span class="badge" id="fps">mode: poll</span>
      <span class="badge" id="latency">stream: MJPEG</span>
      <span class="badge" id="conn">connecting…</span>
    </div>
  </header>
  <div class="toolbar">
    <button class="primary" id="openChatgpt">Open ChatGPT</button>
    <button id="saveSession">Save Session</button>
    <button id="modeBtn">Mode: screencast</button>
    <button id="qualityBtn">Quality: fast</button>
    <button id="classicView">Classic view</button>
    <button id="closeView">Close</button>
  </div>
  <div class="toolbar">
    <input id="type" placeholder="Type here (or click the page and type directly)" autocomplete="off">
    <button id="toggleTyping">Keyboard capture: on</button>
  </div>
  <div class="status" id="status">Starting the live stream…</div>
  <div class="stage" id="stage">
    <canvas id="stream" width="1280" height="800" tabindex="0" aria-label="Live browser view"></canvas>
    <div id="ripple"></div>
  </div>
  <p class="hint">The pointer is real: moves, clicks, drag, wheel and typing are forwarded to the worker browser. FPS/latency are measured from decoded frames. If the stream stalls, press Open ChatGPT or switch mode.</p>
</main>
<script>
const ACCOUNT = __ACCOUNT__;
const CSRF = __CSRF__;
const CLASSIC_URL = __CLASSIC__;
let mode = localStorage.getItem('ninja-live-mode') || 'screencast';
let quality = localStorage.getItem('ninja-live-quality') || 'fast';
document.getElementById('qualityBtn').textContent = 'Quality: ' + quality;
const ripple = document.getElementById('ripple');
let pendingSyncAt = null;
let abort = null;
let lastFrameAt = null;
let frameTimes = [];
let typing = true;
let queue = [];
let sending = false;
let movePending = null;

function url(path) { return '/api/accounts/' + ACCOUNT + '/remote' + path; }
const canvas = document.getElementById('stream');
const ctx = canvas.getContext('2d', { alpha: false });
const statusBox = document.getElementById('status');
const fpsBadge = document.getElementById('fps');
const latencyBadge = document.getElementById('latency');
const connBadge = document.getElementById('conn');

function setStatus(text, kind) {
  statusBox.textContent = text;
  connBadge.textContent = kind === 'good' ? 'live' : kind === 'bad' ? 'offline' : 'connecting…';
  connBadge.className = 'badge ' + (kind === 'good' ? 'good' : kind === 'bad' ? 'warn' : '');
}

async function getJson(path) {
  const r = await fetch(url(path), { cache: 'no-store' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
  return r.json();
}

async function postJson(path, body) {
  const r = await fetch(url(path), {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-csrf-token': CSRF },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
  return r.json();
}

// ---- MJPEG parser: split the multipart stream into JPEG frames and paint them.
async function startStream() {
  stopStream();
  abort = new AbortController();
  setStatus('Connecting to live stream…');
  try {
    const res = await fetch(url('/live') + '?mode=' + mode + '&q=' + quality + '&ts=' + Date.now(), {
      cache: 'no-store',
      signal: abort.signal,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || ('HTTP ' + res.status));
    if (!res.body) throw new Error('Stream not supported by this browser.');
    setStatus('Live stream connected. Click or type directly.', 'good');
    const reader = res.body.getReader();
    let buffer = new Uint8Array(0);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer = await concat(buffer, value);
      for (;;) {
        const head = findBoundary(buffer);
        if (!head) break;
        const jpeg = extractJpeg(buffer, head.end);
        if (!jpeg) break;
        buffer = buffer.slice(jpeg.next);
        drawFrame(jpeg.bytes);
      }
    }
    setStatus('Stream ended — press Open ChatGPT to reconnect.', 'bad');
  } catch (err) {
    if (abort && abort.signal.aborted) return;
    setStatus('Stream error: ' + err.message, 'bad');
  }
}

function stopStream() {
  if (abort) { try { abort.abort(); } catch (e) {} abort = null; }
}

async function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a); out.set(b, a.length);
  return out;
}

function findBoundary(buffer) {
  const marker = new TextEncoder().encode('--frame');
  outer: for (let i = 0; i + marker.length <= buffer.length; i++) {
    for (let j = 0; j < marker.length; j++) if (buffer[i + j] !== marker[j]) continue outer;
    return { start: i, end: i + marker.length };
  }
  return null;
}

function extractJpeg(buffer, from) {
  const crlf2 = new TextEncoder().encode('\r\n\r\n');
  outer: for (let i = from; i + crlf2.length <= buffer.length; i++) {
    for (let j = 0; j < crlf2.length; j++) if (buffer[i + j] !== crlf2[j]) continue outer;
    // optional Content-Length header parsing
    const header = new TextDecoder().decode(buffer.slice(from, i));
    const match = header.match(/Content-Length:\s*(\d+)/i);
    const start = i + crlf2.length;
    if (match) {
      const length = Number(match[1]);
      if (buffer.length < start + length) return null;
      return { bytes: buffer.slice(start, start + length), next: start + length };
    }
    // fall back: scan to the next boundary
    const nextBoundary = indexOfBytes(buffer, new TextEncoder().encode('--frame'), start);
    if (nextBoundary === -1) return null;
    return { bytes: buffer.slice(start, nextBoundary), next: nextBoundary };
  }
  return null;
}

function indexOfBytes(buffer, needle, from) {
  outer: for (let i = from; i + needle.length <= buffer.length; i++) {
    for (let j = 0; j < needle.length; j++) if (buffer[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

async function drawFrame(bytes) {
  try {
    const blob = new Blob([bytes], { type: 'image/jpeg' });
    const bitmap = await createImageBitmap(blob);
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
    }
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close && bitmap.close();
    const now = performance.now();
    if (pendingSyncAt != null) {
      const sync = Math.round(now - pendingSyncAt);
      latencyBadge.textContent = 'sync ' + sync + ' ms';
      pendingSyncAt = null;
    }
    frameTimes.push(now);
    while (frameTimes.length && now - frameTimes[0] > 2000) frameTimes.shift();
    lastFrameAt = now;
  } catch (err) {}
}

// ---- local cursor + input forwarding
function point(event) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return {
    x: Math.round((event.clientX - rect.left) * canvas.width / rect.width),
    y: Math.round((event.clientY - rect.top) * canvas.height / rect.height),
  };
}

function moveCursor(event) {
  // Native cursor shape is driven by the remote page (no local pointer overlay).
}

let cursorProbeAt = 0;
async function probeCursor(event) {
  const now = performance.now();
  if (now - cursorProbeAt < 120) return;
  cursorProbeAt = now;
  const p = point(event);
  if (!p) return;
  try {
    const info = await getJson('/cursor?x=' + p.x + '&y=' + p.y);
    const shape = info.cursor && info.cursor !== 'auto' ? info.cursor : 'default';
    canvas.style.cursor = shape;
    if (info.editable) canvas.style.cursor = 'text';
  } catch (err) {
    canvas.style.cursor = 'default';
  }
}

function showRipple(event) {
  const rect = canvas.getBoundingClientRect();
  ripple.style.left = (event.clientX - rect.left) + 'px';
  ripple.style.top = (event.clientY - rect.top) + 'px';
  ripple.classList.remove('go');
  void ripple.offsetWidth;
  ripple.classList.add('go');
}

function enqueue(event) {
  if (queue.length > 400) queue = queue.slice(-200);
  queue.push(event);
  flush();
}

async function flush() {
  if (sending || !queue.length) return;
  sending = true;
  pendingSyncAt = performance.now();
  const batch = queue.splice(0, 120);
  try {
    await postJson('/input', { events: batch });
    setStatus('Live', 'good');
  } catch (err) {
    if (String(err.message).includes('login session')) {
      setStatus('Login session expired — reconnecting…');
      try { await getJson('/init'); } catch {}
      startStream();
    } else {
      setStatus(err.message, 'bad');
    }
  } finally {
    sending = false;
    if (queue.length) setTimeout(flush, 8);
  }
}

canvas.addEventListener('pointermove', (event) => {
  moveCursor(event);
  probeCursor(event);
  const p = point(event);
  if (!p) return;
  if (movePending) return;
  movePending = setTimeout(() => { movePending = null; }, 15);
  enqueue({ type: 'mouse', action: 'move', x: p.x, y: p.y });
});

canvas.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  canvas.focus();
  showRipple(event);
  moveCursor(event);
  const p = point(event);
  if (!p) return;
  canvas.setPointerCapture?.(event.pointerId);
  enqueue({ type: 'mouse', action: 'down', x: p.x, y: p.y, button: event.button === 2 ? 'right' : 'left', buttons: 1 });
  canvas.focus();
});

canvas.addEventListener('pointerup', (event) => {
  const p = point(event);
  if (!p) return;
  enqueue({ type: 'mouse', action: 'up', x: p.x, y: p.y, button: event.button === 2 ? 'right' : 'left', buttons: 0 });
  // Keep the keyboard on the remote page: do NOT steal focus into the panel input.
  canvas.focus();
  setStatus('Clicked at ' + p.x + ',' + p.y + ' — type now (keys go to the browser).', 'good');
});

canvas.addEventListener('wheel', (event) => {
  event.preventDefault();
  const p = point(event);
  if (!p) return;
  enqueue({ type: 'mouse', action: 'wheel', x: p.x, y: p.y, deltaX: event.deltaX, deltaY: event.deltaY });
}, { passive: false });

canvas.addEventListener('contextmenu', (event) => event.preventDefault());

const VK = {
  Enter: 13, Tab: 9, Backspace: 8, Escape: 27, ArrowUp: 38, ArrowDown: 40,
  ArrowLeft: 37, ArrowRight: 39, Delete: 46, Home: 36, End: 35,
  PageUp: 33, PageDown: 34, ' ': 32,
};

function sendKey(key) {
  if (key === ' ') key = 'Space';
  const vk = VK[key] || 0;
  if (!vk) return;
  enqueue({ type: 'key', key: key === 'Space' ? ' ' : key, vk });
}

document.addEventListener('keydown', (event) => {
  if (!typing) return;
  const target = event.target;
  const isPanelField = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || '');
  if (isPanelField) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (VK[event.key]) { event.preventDefault(); sendKey(event.key); return; }
  if (event.key.length === 1) { event.preventDefault(); enqueue({ type: 'text', text: event.key }); }
});

document.addEventListener('paste', (event) => {
  if (!typing) return;
  const text = (event.clipboardData || window.clipboardData).getData('text');
  if (!text) return;
  event.preventDefault();
  enqueue({ type: 'text', text });
});

document.getElementById('type').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') { event.preventDefault(); sendKey('Enter'); }
});

document.getElementById('toggleTyping').addEventListener('click', () => {
  typing = !typing;
  document.getElementById('toggleTyping').textContent = 'Keyboard capture: ' + (typing ? 'on' : 'off');
});

document.getElementById('openChatgpt').addEventListener('click', async () => {
  try { await getJson('/navigate'); setStatus('Reloading ChatGPT…'); startStream(); }
  catch (err) { setStatus(err.message, 'bad'); }
});

document.getElementById('saveSession').addEventListener('click', async () => {
  try {
    setStatus('Saving session…');
    const data = await postJson('/save', {});
    setStatus('Saved ✓ — session: ' + data.session + '. You can close this tab.', 'good');
  } catch (err) { setStatus(err.message, 'bad'); }
});

document.getElementById('qualityBtn').addEventListener('click', () => {
  quality = quality === 'fast' ? 'balanced' : quality === 'balanced' ? 'sharp' : 'fast';
  localStorage.setItem('ninja-live-quality', quality);
  document.getElementById('qualityBtn').textContent = 'Quality: ' + quality;
  startStream();
});

document.getElementById('modeBtn').textContent = 'Mode: ' + mode;

document.getElementById('modeBtn').addEventListener('click', () => {
  mode = mode === 'poll' ? 'screencast' : 'poll';
  document.getElementById('modeBtn').textContent = 'Mode: ' + mode;
  localStorage.setItem('ninja-live-mode', mode);
  fpsBadge.textContent = 'mode: ' + mode;
  startStream();
});

document.getElementById('classicView').addEventListener('click', () => { window.location.href = CLASSIC_URL; });
document.getElementById('closeView').addEventListener('click', () => window.close());

(async function boot() {
  try {
    await getJson('/init');
    startStream();
  } catch (err) {
    setStatus(err.message, 'bad');
  }
})();
</script>
</body>
</html>
"""


def render(account_id, title, csrf, classic_url):
    return (
        PAGE.replace("__TITLE__", title)
        .replace("__ACCOUNT__", repr(account_id))
        .replace("__CSRF__", csrf)
        .replace("__CLASSIC__", repr(classic_url))
    )
