// ninja:plan-probe-v2 — read the signed-in account's plan from ChatGPT's own web API.
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

module.exports = router;