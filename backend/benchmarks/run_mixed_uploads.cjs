// Actual frontend uploads, with an arrival ledger and bounded pool of prepared tabs.
const fs = require('node:fs');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { chromium } = require('../../frontend/node_modules/playwright');
const { measureUpload } = require('./upload_measurement.cjs');
const { arrivalOffsets, validateSpec, runSchedule, sleep } = require('./mixed_load_core.cjs');

async function main() {
  const folder = process.argv[2];
  const spec = JSON.parse(fs.readFileSync(path.join(folder, 'spec.json'), 'utf8'));
  validateSpec(spec);
  const append = row => fs.appendFileSync(path.join(folder, 'uploads.jsonl'), JSON.stringify(row) + '\n');
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
  const slots = [];
  let finished = false;
  async function prepare(slot) {
    await slot.page.goto(`${spec.origin}/bench-${spec.mode}/`);
    await slot.page.getByRole('button', { name: 'Upload video', exact: true }).first().click();
    slot.dialog = slot.page.getByRole('dialog');
    await slot.dialog.locator('input[type=file]').setInputFiles(spec.file);
    await slot.dialog.getByLabel('Title', { exact: true }).fill(`mixed ${spec.name} ${slot.index} ${Date.now()}`);
    slot.available = true;
  }
  try {
    // Sequential setup avoids an artificial preflight API burst.
    for (let index = 0; index < spec.pool_size; index++) {
      const context = await browser.newContext({ storageState: '/bench/session.json', ignoreHTTPSErrors: true });
      const page = await context.newPage();
      page.setDefaultTimeout(30000);
      const cdp = await context.newCDPSession(page);
      await cdp.send('Network.enable');
      await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40,
        uploadThroughput: 20e6 / 8, downloadThroughput: 100e6 / 8 });
      const slot = { context, page, index, available: false };
      slots.push(slot); await prepare(slot);
    }
    fs.writeFileSync(path.join(folder, 'upload-ready.json'), JSON.stringify({ slots: slots.length, browser: browser.version(), node: process.version }));
    while (!fs.existsSync(path.join(folder, 'go.json'))) await sleep(100);
    const go = JSON.parse(fs.readFileSync(path.join(folder, 'go.json'), 'utf8'));
    const start = performance.now() / 1000 + (go.start_epoch - Date.now() / 1000);
    const offsets = arrivalOffsets(spec.uploads_per_minute / 60, spec.duration_seconds);
    await runSchedule(offsets, async arrival => {
      const slot = slots.find(s => s.available);
      if (!slot) return append({ ...arrival, outcome: 'not_started', failure_stage: 'pool_full', bytes: fs.statSync(spec.file).size });
      slot.available = false;
      const began = performance.now();
      const startedEpoch = Date.now() / 1000;
      fs.appendFileSync(path.join(folder, 'upload-starts.jsonl'), JSON.stringify({ ...arrival, slot: slot.index, started_epoch: startedEpoch }) + '\n');
      try {
        const result = await measureUpload(slot.page, { timeoutMs: spec.upload_timeout_seconds * 1000,
          start: () => slot.dialog.getByRole('button', { name: 'Upload video', exact: true }).click(), began });
        if (result.observed_mode && result.observed_mode !== spec.mode) throw new Error('Wrong frontend route');
        append({ ...arrival, slot: slot.index, started_epoch: startedEpoch, completed_epoch: Date.now() / 1000,
          bytes: fs.statSync(spec.file).size, ...result });
      } catch { append({ ...arrival, slot: slot.index, started_epoch: startedEpoch, outcome: 'failed', failure_stage: 'driver' }); }
      finally {
        if (performance.now() / 1000 < start + spec.baseline_seconds + spec.duration_seconds) {
          try { await prepare(slot); } catch { slot.available = false; }
        }
      }
    }, { start: start + spec.baseline_seconds });
    // Keep tab/background traffic comparable for the entire observation window.
    await sleep((start + spec.baseline_seconds + spec.duration_seconds + spec.recovery_seconds - performance.now() / 1000) * 1000);
    finished = true;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(folder, 'upload-completion.json'), JSON.stringify({ finished }));
  }
}
main().catch(error => { console.error(`Mixed upload driver failed (${error.name})`); process.exitCode = 1; });
