// ninja:live-view — real-time frames, trusted input, hover cursor + click feedback.
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

module.exports = router;