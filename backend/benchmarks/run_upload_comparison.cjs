// Usage: node benchmarks/run_upload_comparison.cjs --manifest <staging.json> --output <new-dir>
// Run from backend/. The manifest defines targets explicitly; this creates staging videos.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../frontend/node_modules/playwright');
const { measureUpload, summarize } = require('./upload_measurement.cjs');
const { validatePipeline, measurePipeline, summarizePipeline } = require('./pipeline_measurement.cjs');
const { createBrowserProbes } = require('./pipeline_browser_probes.cjs');

function options(argv) {
  const flags = {};
  for (let i = 0; i < argv.length; i += 2) flags[argv[i]] = argv[i + 1];
  if (!flags['--manifest'] || !flags['--output']) throw new Error('Provide --manifest and a new --output directory');
  const manifest = JSON.parse(fs.readFileSync(flags['--manifest'], 'utf8'));
  if (manifest.environment !== 'staging') throw new Error('Manifest must identify an isolated staging environment');
  for (const mode of ['api', 'direct']) {
    const url = new URL(manifest.targets?.[mode]);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Use a plain app URL without credentials, query strings or fragments');
  }
  if (!Array.isArray(manifest.files) || !manifest.files.length || !manifest.files.every(file => fs.statSync(file).isFile())) throw new Error('Provide existing video files');
  if (new Set(manifest.files.map(file => path.basename(file))).size !== manifest.files.length) throw new Error('Use distinct fixture filenames so cohorts cannot be combined accidentally');
  for (const name of ['repetitions', 'concurrency', 'timeout_seconds']) {
    if (!Number.isInteger(manifest[name]) || manifest[name] < 1) throw new Error(`${name} must be a positive integer`);
  }
  if (manifest.concurrency > 10) throw new Error('This pilot runner is capped at 10 simultaneous uploads');
  const network = manifest.network || { name: 'unshaped' };
  if (!network.name) throw new Error('Name the network profile');
  if (network.name !== 'unshaped' && ![network.latency_ms, network.upload_mbps, network.download_mbps].every(n => Number.isFinite(n) && n > 0)) throw new Error('Shaped networks require positive latency_ms, upload_mbps and download_mbps');
  const pipeline = validatePipeline(manifest);
  return { manifest, output: path.resolve(flags['--output']), network, pipeline };
}

async function main() {
  const { manifest, output, network, pipeline } = options(process.argv.slice(2));
  // Exclusive directory preserves all failed and completed run evidence.
  fs.mkdirSync(output);
  const runId = new Date().toISOString().replace(/[:.]/g, '-');
  fs.writeFileSync(path.join(output, 'run.json'), JSON.stringify({ run_id: runId,
    environment: 'staging', targets: manifest.targets, commit: manifest.commit || 'unrecorded',
    files: manifest.files.map(file => ({ name: path.basename(file), bytes: fs.statSync(file).size })),
    network, repetitions: manifest.repetitions, concurrency: manifest.concurrency,
    timeout_seconds: manifest.timeout_seconds, readiness: pipeline || 'not measured; upload acceptance only',
    credentials: 'omitted', order: pipeline ? 'single wave; pair and reset externally' : 'alternating AB/BA by repetition' }, null, 2));
  const browser = await chromium.launch({ headless: true,
    ...(process.env.VINDEX_BROWSER_CHANNEL ? { channel: process.env.VINDEX_BROWSER_CHANNEL } : {}) });
  const runPath = path.join(output, 'run.json');
  fs.writeFileSync(runPath, JSON.stringify({ ...JSON.parse(fs.readFileSync(runPath, 'utf8')),
    browser_version: browser.version(), node_version: process.version, platform: process.platform }, null, 2));
  const rows = [];
  let completed = false;
  let controlContext, controlInfo;
  const control = manifest.benchmark_control;
  async function controlRequest(action, data) {
    const response = await controlContext.request.post(`${control.origin}/bench/control/${action}`, { data, timeout: 180000 });
    if (!response.ok()) throw new Error(`Benchmark control ${action} returned HTTP ${response.status()}`);
    return response.json();
  }
  try {
    let benchCookie;
    if (control) {
      if (['api', 'direct'].some(mode => new URL(manifest.targets[mode]).origin !== control.origin)) throw new Error('Control and app origins must match');
      const secret = JSON.parse(fs.readFileSync(control.secret_file, 'utf8'));
      if (typeof secret.token !== 'string' || secret.token.length < 32) throw new Error('Invalid isolated benchmark token');
      benchCookie = { name: 'vindex_bench', value: secret.token, url: control.origin, secure: true, httpOnly: true, sameSite: 'Lax' };
      controlContext = await browser.newContext({ ignoreHTTPSErrors: true });
      await controlContext.addCookies([benchCookie]);
      const info = await controlContext.request.get(`${control.origin}/bench/control/info`);
      if (!info.ok()) throw new Error(`Isolated benchmark unavailable: HTTP ${info.status()}`);
      controlInfo = await info.json();
      if (!controlInfo.isolated || controlInfo.code_commit !== manifest.commit) throw new Error('Isolated server or source identity mismatch');
      fs.writeFileSync(path.join(output, 'server.json'), JSON.stringify(controlInfo, null, 2));
    }
    for (const file of manifest.files) {
      const sha256 = crypto.createHash('sha256');
      for await (const chunk of fs.createReadStream(file)) sha256.update(chunk);
      const fileDigest = sha256.digest('hex');
      fs.appendFileSync(path.join(output, 'fixtures.jsonl'), JSON.stringify({ file: path.basename(file), sha256: fileDigest }) + '\n');
      for (let repetition = 0; repetition < manifest.repetitions; repetition++) {
        for (const mode of (pipeline ? [pipeline.mode] : (repetition % 2 ? ['direct', 'api'] : ['api', 'direct']))) {
          // Prepare every page before this wave starts so setup does not skew concurrency.
          const clients = await Promise.all(Array.from({ length: manifest.concurrency }, async (_, client) => {
            let context;
            try {
              context = await browser.newContext({ ...(manifest.storage_state ? { storageState: manifest.storage_state } : {}),
                ...(control || pipeline?.tls_mode === 'self_signed_isolated' ? { ignoreHTTPSErrors: true } : {}) });
              if (benchCookie) await context.addCookies([benchCookie]);
              const page = await context.newPage();
              page.setDefaultTimeout(15000);
              if (network.name !== 'unshaped') {
                const cdp = await context.newCDPSession(page);
                await cdp.send('Network.enable');
                await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: network.latency_ms,
                  uploadThroughput: network.upload_mbps * 1000000 / 8, downloadThroughput: network.download_mbps * 1000000 / 8 });
              }
              await page.goto(manifest.targets[mode]);
              await page.getByRole('button', { name: 'Upload video', exact: true }).first().click();
              const dialog = page.getByRole('dialog');
              await dialog.locator('input[type=file]').setInputFiles(file);
              await dialog.getByLabel('Title', { exact: true }).fill(`Upload experiment ${runId} ${mode} ${repetition}-${client}`);
              return { context, page, dialog, client };
            } catch { return { context, client, setupFailed: true }; }
          }));
          const waveId = `${runId}-${path.basename(file)}-${repetition}-${mode}`;
          if (control) await controlRequest('start', { wave: waveId });
          const waveStart = performance.now();
          const wave = await Promise.all(clients.map(async ({ context, page, dialog, client, setupFailed }) => {
            try {
              const start = () => dialog.getByRole('button', { name: 'Upload video', exact: true }).click();
              const result = setupFailed ? { outcome: 'failed', failure_stage: 'setup', upload_seconds: null } :
                pipeline ? await measurePipeline(page, { start, uploadTimeoutMs: manifest.timeout_seconds * 1000,
                  config: pipeline, probes: createBrowserProbes(page), onEvent: event => {
                    fs.appendFileSync(path.join(output, 'milestones.jsonl'), JSON.stringify({
                      run_id: runId, mode, file: path.basename(file), repetition, client, ...event }) + '\n');
                  } }) : await measureUpload(page, { timeoutMs: manifest.timeout_seconds * 1000, start });
              if (result.observed_mode && result.observed_mode !== mode) {
                Object.assign(result, { outcome: 'failed', failure_stage: 'wrong_frontend_upload_mode', upload_seconds: null });
              }
              const row = { run_id: runId, mode, file: path.basename(file), bytes: fs.statSync(file).size,
                repetition, client, concurrency: manifest.concurrency, network: network.name,
                ...(pipeline ? { worker_state: pipeline.worker_state } : {}), ...result };
              rows.push(row);
              fs.appendFileSync(path.join(output, 'attempts.jsonl'), JSON.stringify(row) + '\n');
              return row;
            } finally { await context?.close(); }
          }));
          fs.appendFileSync(path.join(output, 'waves.jsonl'), JSON.stringify({ mode, file: path.basename(file), repetition,
            elapsed_seconds: (performance.now() - waveStart) / 1000, ...summarize(wave),
            ...(pipeline ? { pipeline: summarizePipeline(wave) } : {}) }) + '\n');
          if (control) {
            const resources = await controlRequest('end', { wave: waveId });
            fs.appendFileSync(path.join(output, 'resources.jsonl'), JSON.stringify({ mode, file: path.basename(file), repetition, ...resources }) + '\n');
            const verification = await controlRequest('verify-cleanup', { videos: wave.filter(r => r.video_id).map(r => r.video_id),
              bytes: fs.statSync(file).size, sha256: fileDigest });
            fs.appendFileSync(path.join(output, 'verification.jsonl'), JSON.stringify({ mode, file: path.basename(file), repetition, ...verification }) + '\n');
          }
          console.log(`${mode} ${path.basename(file)} wave ${repetition + 1}: ${wave.filter(r => r.outcome === 'accepted').length}/${wave.length} accepted`);
          if (manifest.stop_after_failed_wave && wave.some(r => r.outcome !== 'accepted')) throw new Error('Stopping after failed wave; evidence retained');
        }
      }
    }
    completed = true;
  } finally {
    await controlContext?.close();
    await browser.close();
    const summary = {};
    for (const row of rows) {
      const key = JSON.stringify([row.mode, row.file, row.bytes, row.concurrency, row.network]);
      (summary[key] ||= []).push(row);
    }
    fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(Object.fromEntries(
      Object.entries(summary).map(([key, group]) => [key, { ...summarize(group),
        ...(pipeline ? { pipeline: summarizePipeline(group) } : {}) }])), null, 2));
    fs.writeFileSync(path.join(output, 'completion.json'), JSON.stringify({
      finished_all_planned_waves: completed, recorded_attempts: rows.length,
      planned_attempts: manifest.files.length * manifest.repetitions * manifest.concurrency * (pipeline ? 1 : 2),
      all_attempts_accepted: completed && rows.length > 0 && rows.every(row => row.outcome === 'accepted'),
      ...(pipeline ? { all_attempts_ready: completed && rows.length === manifest.concurrency &&
        rows.every(row => row.outcome === 'accepted' && row.readiness_outcome === 'ready'),
        integrity_and_cleanup: 'not verified by browser runner; retain evidence and reconcile before cleanup' } : {})
    }, null, 2));
  }
  if (rows.some(row => row.outcome !== 'accepted' || (pipeline && row.readiness_outcome !== 'ready'))) process.exitCode = 1;
}

// Playwright error messages can contain cookies, headers and signed URLs.
if (require.main === module) main().catch(error => { console.error(`Comparison stopped (${error.name}); retained artifacts identify the completed stage.`); process.exitCode = 1; });
module.exports = { options };
