// ninja:password-fallback-v2
// OpenAI inserts an email-verification screen after the email step; it offers an official
// "Continue with password" button. The screen appears with a delay, so poll briefly for
// either the button or the password field instead of checking once.
async function clickContinueWithPassword(page, timeoutMs) {
  try {
    const button = page.getByRole('button', { name: /continue with password/i }).first();
    const password = page.locator('input[type="password"]').first();
    const deadline = Date.now() + (timeoutMs || 12000);
    while (Date.now() < deadline) {
      if (await password.isVisible().catch(() => false)) return false;
      if (await button.isVisible().catch(() => false)) {
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
