// ninja:settings-probe-v2 — read Settings -> Account (plan + workspace id).
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

module.exports = router;