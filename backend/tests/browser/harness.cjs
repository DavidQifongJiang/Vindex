// Shared by the browser suites. Set VINDEX_BROWSER_CHANNEL=msedge to use an installed browser;
// CI leaves it unset and runs Playwright's own Chromium.
const fs = require('node:fs');
const path = require('node:path');

const output = () => process.env.VINDEX_VERIFY_OUTPUT;

function launchOptions() {
  const channel = process.env.VINDEX_BROWSER_CHANNEL;
  return { headless: true, ...(channel ? { channel } : {}) };
}

// A missing results file is how CI detects a suite that never ran.
function recordPass(name) {
  if (!output()) return;
  fs.mkdirSync(output(), { recursive: true });
  fs.writeFileSync(path.join(output(), 'results.json'), JSON.stringify([{ name, status: 'passed' }], null, 2));
}

async function captureFailure(browser) {
  if (!output()) return;
  fs.mkdirSync(output(), { recursive: true });
  const page = browser.contexts().flatMap(context => context.pages())[0];
  await page?.screenshot({ path: path.join(output(), 'failure.png'), fullPage: true }).catch(() => {});
}

module.exports = { launchOptions, recordPass, captureFailure };
