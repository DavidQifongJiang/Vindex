// Parent passes a short-lived upload policy through stdin. Never log that policy.
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../frontend/node_modules/playwright');

(async () => {
  let browser;
  try {
    const probe = JSON.parse(fs.readFileSync(0, 'utf8'));
    browser = await chromium.launch({ headless: true,
      ...(process.env.VINDEX_BROWSER_CHANNEL ? { channel: process.env.VINDEX_BROWSER_CHANNEL } : {}) });
    const page = await browser.newPage();
    // A blank document at the allowed origin tests browser CORS without signing
    // into the app, touching API state, or executing the live frontend.
    const origin = new URL(probe.origin).origin;
    await page.route(origin + '/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>S3 upload probe</title>' }));
    await page.goto(origin + '/');
    const result = await page.evaluate(async ({ upload, payload }) => {
      try {
        const form = new FormData();
        for (const [key, value] of Object.entries(upload.fields)) form.append(key, value);
        const bytes = Uint8Array.from(atob(payload), c => c.charCodeAt(0));
        form.append('file', new Blob([bytes], { type: 'video/mp4' }), 'probe.mp4');
        const response = await fetch(upload.url, { method: 'POST', body: form, signal: AbortSignal.timeout(30000) });
        return { ok: response.ok, status: response.status };
      } catch { return { ok: false, error: 'BrowserUploadOrCorsFailed' }; }
    }, probe);
    console.log(JSON.stringify(result));
  } catch { console.log(JSON.stringify({ ok: false, error: 'BrowserProbeFailed' })); }
  finally { await browser?.close(); }
})();
