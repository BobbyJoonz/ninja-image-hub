// ninja:pages-routes — diagnostics for project routing.
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

module.exports = router;