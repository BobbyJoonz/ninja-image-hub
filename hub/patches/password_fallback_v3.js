// ninja:password-fallback-v3
// The "Continue with password" button can live inside an iframe (auth.openai.com).
// Search every frame, not just the main one, and poll briefly for either the button
// or the password field.
async function clickContinueWithPassword(page, timeoutMs) {
  const findButton = async () => {
    for (const frame of page.frames()) {
      try {
        const button = frame.getByRole('button', { name: /continue with password/i }).first();
        if (await button.isVisible().catch(() => false)) return button;
      } catch (err) { /* frame may be detached */ }
    }
    return null;
  };
  const passwordVisible = async () => {
    for (const frame of page.frames()) {
      try {
        if (await frame.locator('input[type="password"]').first().isVisible().catch(() => false)) return true;
      } catch (err) { /* frame may be detached */ }
    }
    return false;
  };
  try {
    const deadline = Date.now() + (timeoutMs || 12000);
    while (Date.now() < deadline) {
      if (await passwordVisible()) return false;
      const button = await findButton();
      if (button) {
        console.log('[auto-login] Verification screen detected — clicking Continue with password');
        await button.click();
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        return true;
      }
      await page.waitForTimeout(400);
    }
    return false;
  } catch (err) {
    return false;
  }
}
