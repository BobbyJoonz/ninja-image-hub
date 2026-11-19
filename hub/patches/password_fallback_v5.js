// ninja:password-fallback-v5
// Single definition. Find the "Continue with password" button by visible TEXT across
// every frame (a getByRole name regex can miss it when the label wraps), then click.
async function clickContinueWithPassword(page, timeoutMs) {
  const pickButton = async () => {
    for (const frame of page.frames()) {
      let candidates = [];
      try {
        candidates = await frame.locator('button, a, [role="button"]').all();
      } catch (err) { continue; }
      for (const el of candidates) {
        try {
          const text = ((await el.textContent()) || '').replace(/\s+/g, ' ').trim().toLowerCase();
          if (text.includes('continue with password') && await el.isVisible()) return el;
        } catch (err) { /* detached */ }
      }
    }
    return null;
  };
  const passwordVisible = async () => {
    for (const frame of page.frames()) {
      try {
        if (await frame.locator('input[type="password"]').first().isVisible().catch(() => false)) return true;
      } catch (err) { /* detached */ }
    }
    return false;
  };
  try {
    const deadline = Date.now() + (timeoutMs || 60000);
    while (Date.now() < deadline) {
      if (await passwordVisible()) return false;
      const button = await pickButton();
      if (button) {
        console.log('[auto-login] Verification screen detected — clicking Continue with password');
        await button.click();
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        return true;
      }
      await page.waitForTimeout(500);
    }
    return false;
  } catch (err) {
    return false;
  }
}
