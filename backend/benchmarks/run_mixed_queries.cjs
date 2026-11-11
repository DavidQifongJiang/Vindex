// Independent search generator: real API queries; no shared browser upload event loop.
const fs = require('node:fs');
const path = require('node:path');
const https = require('node:https');
const { performance, monitorEventLoopDelay } = require('node:perf_hooks');
const { arrivalOffsets, phaseAt, validateSpec, runSchedule, sleep } = require('./mixed_load_core.cjs');

async function main() {
  const folder = process.argv[2];
  const spec = JSON.parse(fs.readFileSync(path.join(folder, 'spec.json'), 'utf8'));
  validateSpec(spec);
  const token = JSON.parse(fs.readFileSync('/bench/private.json', 'utf8')).token;
  const agent = new https.Agent({ keepAlive: true, maxSockets: 64, rejectUnauthorized: false });
  const delay = monitorEventLoopDelay({ resolution: 20 }); delay.enable();
  const request = ({ index, scheduled_seconds, lag_seconds }) => new Promise(resolve => {
    const videoId = spec.seeds[index % spec.seeds.length];
    const query = spec.queries[Math.floor(index / spec.seeds.length) % spec.queries.length];
    const body = JSON.stringify({ query, algorithm: 'qdrant_embedding' });
    const began = performance.now();
    const row = { index, scheduled_seconds, lag_seconds, phase: phaseAt(scheduled_seconds, spec), video_id: videoId, query_index: Math.floor(index / spec.seeds.length) % spec.queries.length };
    let done = false;
    const finish = (outcome, status, serverSeconds) => {
      if (done) return; done = true; clearTimeout(timer);
      fs.appendFileSync(path.join(folder, 'queries.jsonl'), JSON.stringify({ ...row, outcome, http_status: status,
        seconds: (performance.now() - began) / 1000, server_seconds: serverSeconds ?? null }) + '\n');
      resolve();
    };
    const req = https.request(`${spec.origin}/videos/${videoId}/search`, { method: 'POST', agent,
      headers: { Cookie: `vindex_bench=${token}`, 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, response => {
      let text = '';
      response.on('data', chunk => { text += chunk; if (text.length > 1e6) req.destroy(); });
      response.on('end', () => {
        try {
          const result = JSON.parse(text);
          const good = response.statusCode === 200 && result.video_id === videoId && result.results?.some(hit =>
            typeof hit.text === 'string' && /vector\s+search/i.test(hit.text) && hit.start <= 5 && hit.end >= 0);
          finish(good ? 'success' : 'invalid_response', response.statusCode, result.metrics?.search_latency_seconds);
        } catch { finish('invalid_response', response.statusCode); }
      });
      response.on('error', () => finish('transport_error', response.statusCode));
    });
    const timer = setTimeout(() => { finish('timeout', null); req.destroy(); }, 5000);
    req.on('error', () => finish('transport_error', null));
    req.end(body);
  });
  let finished = false;
  try {
    while (!fs.existsSync(path.join(folder, 'go.json'))) await sleep(100);
    const go = JSON.parse(fs.readFileSync(path.join(folder, 'go.json'), 'utf8'));
    const start = performance.now() / 1000 + go.start_epoch - Date.now() / 1000;
    await runSchedule(arrivalOffsets(spec.search_rps, spec.baseline_seconds + spec.duration_seconds + spec.recovery_seconds), request, { start });
    finished = true;
  } finally {
    delay.disable(); agent.destroy();
    fs.writeFileSync(path.join(folder, 'query-completion.json'), JSON.stringify({ finished,
      event_loop_p99_seconds: delay.percentile(99) / 1e9, event_loop_max_seconds: delay.max / 1e9, node: process.version }));
  }
}
main().catch(error => { console.error(`Mixed query driver failed (${error.name})`); process.exitCode = 1; });
