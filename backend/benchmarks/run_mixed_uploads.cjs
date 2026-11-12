// Actual frontend uploads, with an arrival ledger and bounded pool of prepared tabs.
const fs = require('node:fs');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { chromium } = require('../../frontend/node_modules/playwright');
const { measureUpload } = require('./upload_measurement.cjs');
const { arrivalOffsets, validateSpec, runSchedule, sleep, confirmedNegativeStart } = require('./mixed_load_core.cjs');
const { createBrowserProbes } = require('./pipeline_browser_probes.cjs');
const { measurePipeline } = require('./pipeline_measurement.cjs');

async function main() {
  const folder = process.argv[2];
  const spec = JSON.parse(fs.readFileSync(path.join(folder, 'spec.json'), 'utf8'));
  validateSpec(spec);
  if (spec.sentinel_file && (!spec.sentinel_file.startsWith('/bench/fixtures/') || spec.sentinel_file.includes('..') || spec.pool_size < 2)) {
    throw new Error('Invalid long-video sentinel configuration');
  }
  const append = row => fs.appendFileSync(path.join(folder, 'uploads.jsonl'), JSON.stringify(row) + '\n');
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
  const slots = [];
  let finished = false;
  async function prepare(slot) {
    await slot.page.goto(`${spec.origin}/bench-${spec.mode}/`);
    await slot.page.getByRole('button', { name: 'Upload video', exact: true }).first().click();
    slot.dialog = slot.page.getByRole('dialog');
    await slot.dialog.locator('input[type=file]').setInputFiles(slot.sentinel ? spec.sentinel_file : spec.file);
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
      const slot = { context, page, index, available: false, sentinel: !!spec.sentinel_file && index === 0 };
      slots.push(slot); await prepare(slot);
    }
    fs.writeFileSync(path.join(folder, 'upload-ready.json'), JSON.stringify({ slots: slots.length, browser: browser.version(), node: process.version }));
    while (!fs.existsSync(path.join(folder, 'go.json'))) await sleep(100);
    const go = JSON.parse(fs.readFileSync(path.join(folder, 'go.json'), 'utf8'));
    const start = performance.now() / 1000 + (go.start_epoch - Date.now() / 1000);
    const offsets = arrivalOffsets(spec.uploads_per_minute / 60, spec.duration_seconds);
    const sentinel = spec.sentinel_file ? runSchedule([0], async arrival => {
      const slot = slots[0]; slot.available = false;
      const began = performance.now(), startedEpoch = Date.now() / 1000;
      const appendSentinel = value => fs.appendFileSync(path.join(folder, 'sentinel.jsonl'), JSON.stringify(value) + '\n');
      let acceptanceRecorded = false;
      try {
        const deadline = start + spec.baseline_seconds + spec.duration_seconds + spec.recovery_seconds;
        let lastNegative = null;
        const result = await measurePipeline(slot.page, {
          start: () => slot.dialog.getByRole('button', { name: 'Upload video', exact: true }).click(),
          uploadTimeoutMs: spec.upload_timeout_seconds * 1000,
          config: { deadline_seconds: Math.max(1, deadline - performance.now() / 1000), poll_interval_ms: 2000,
            probe_timeout_ms: 5000, search: { query: 'vector search', expected_text: 'vector search',
              start_seconds: 0, end_seconds: 900, tolerance_seconds: 0 } },
          probes: createBrowserProbes(slot.page),
          onEvent: event => {
            if (event.milestone === 'upload') {
              if (event.observed_mode && event.observed_mode !== spec.mode) throw new Error('Wrong sentinel route');
              appendSentinel({ ...arrival, started_epoch: startedEpoch, completed_epoch: Date.now() / 1000,
                bytes: fs.statSync(spec.sentinel_file).size, ...event });
              acceptanceRecorded = true;
            } else {
              fs.appendFileSync(path.join(folder, 'sentinel-probes.jsonl'), JSON.stringify(event) + '\n');
              lastNegative = confirmedNegativeStart(event, lastNegative);
            }
          }
        });
        fs.writeFileSync(path.join(folder, 'sentinel-readiness.json'), JSON.stringify({
          video_id: result.video_id, outcome: result.searchable?.outcome || 'not_started',
          after_acceptance_seconds: result.searchable?.after_acceptance_seconds ?? null,
          last_negative_seconds: lastNegative === null ? null : lastNegative - result.upload_seconds,
          click_to_ready_seconds: result.searchable?.observed_seconds ?? null,
          observation_seconds: result.pipeline_elapsed_seconds, full_pipeline: result }));
      } catch {
        if (!acceptanceRecorded) appendSentinel({ ...arrival, started_epoch: startedEpoch, outcome: 'failed', failure_stage: 'sentinel_driver' });
        fs.writeFileSync(path.join(folder, 'sentinel-error.json'), JSON.stringify({ failed: true, acceptanceRecorded }));
      }
    }, { start: start + spec.baseline_seconds }) : Promise.resolve();
    await runSchedule(offsets, async arrival => {
      const slot = slots.find(s => s.available && !s.sentinel);
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
    await sentinel;
    // Keep tab/background traffic comparable for the entire observation window.
    await sleep((start + spec.baseline_seconds + spec.duration_seconds + spec.recovery_seconds - performance.now() / 1000) * 1000);
    finished = true;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(folder, 'upload-completion.json'), JSON.stringify({ finished }));
  }
}
main().catch(error => { console.error(`Mixed upload driver failed (${error.name})`); process.exitCode = 1; });
