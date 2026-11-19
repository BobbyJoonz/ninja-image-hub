// ninja:totp-all-frames
// The MFA code field and its submit button can be rendered inside an iframe
// (auth.openai.com). The upstream findCodeField/submitCode search only the main
// frame, so auto-login reports "code field was not found" even when otp:true.
// These wrappers search every frame and return locators bound to the owning frame.
async function ninjaFindCodeField(page) {
  const singleSelectors = [
    'input[name="code"]', 'input[autocomplete="one-time-code"]',
    'input[inputmode="numeric"]', 'input[type="tel"]', 'input[maxlength="6"]',
  ];
  for (const frame of page.frames()) {
    for (const selector of singleSelectors) {
      try {
        const loc = frame.locator(selector);
        const n = await loc.count();
        for (let i = 0; i < n; i += 1) {
          const item = loc.nth(i);
          if (!(await item.isVisible().catch(() => false))) continue;
          if ((await item.getAttribute('maxlength').catch(() => null)) === '1') continue;
          if (selector === 'input[maxlength="6"]') {
            const type = ((await item.getAttribute('type').catch(() => '')) || '').toLowerCase();
            if (type !== '' && type !== 'text' && type !== 'tel' && type !== 'number') continue;
          }
          console.log('[auto-login] TOTP code field found in frame:', frame.url());
          return { mode: 'single', fields: [item] };
        }
      } catch (err) { /* frame may be detached */ }
    }
  }
  return null;
}
