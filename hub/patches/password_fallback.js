// ninja:password-fallback
// OpenAI sometimes inserts an "email verification" screen after the email step even
// when the account has a password. It offers a "Continue with password" button — an
// official alternative path, not a bypass. Click it so the normal password/TOTP flow
// can continue instead of failing with device_verification.
async function clickContinueWithPassword(page) {
  try {
    const btn = page.getByRole('button', { name: /continue with password/i }).first();
    if (!btn || !(await btn.isVisible().catch(() => false))) return false;
    console.log('[auto-login] Email verification screen — clicking Continue with password');
    await btn.click();
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    return true;
  } catch (err) {
    return false;
  }
}
