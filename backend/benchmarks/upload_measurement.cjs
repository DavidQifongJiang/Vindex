// Shared browser measurement and statistics. Never retain signed URLs or tokens.
const { performance } = require('node:perf_hooks');

function summarize(rows) {
  const accepted = rows.filter(row => row.outcome === 'accepted');
  const values = accepted.map(row => row.upload_seconds).sort((a, b) => a - b);
  const percentile = fraction => values.length ? values[Math.ceil(values.length * fraction) - 1] : null;
  const n = rows.length, successes = accepted.length, z = 1.959963984540054;
  const p = n ? successes / n : 0, denominator = 1 + z * z / (n || 1);
  const center = (p + z * z / (2 * (n || 1))) / denominator;
  const margin = z * Math.sqrt(p * (1 - p) / (n || 1) + z * z / (4 * (n || 1) ** 2)) / denominator;
  return { attempted: n, accepted: successes, failed: n - successes,
    acceptance_rate: n ? p : null,
    acceptance_rate_95pct_wilson: n ? [Math.max(0, center - margin), Math.min(1, center + margin)] : null,
    accepted_p50_seconds: percentile(0.5), accepted_p95_seconds: percentile(0.95),
    tail_sample_warning: successes < 100 ? 'Fewer than 100 accepted attempts; p95 is exploratory' : null };
}

async function measureUpload(page, { timeoutMs, start, clock = () => performance.now(), began = clock() }) {
  let intentStart, completionStart, storageUrl, intentRead, settled = false, timer;
  const requestTimes = new WeakMap();
  const row = { outcome: 'failed', failure_stage: 'browser', upload_seconds: null };
  let resolve;
  const done = new Promise(r => { resolve = r; });
  const finish = values => {
    if (settled) return;
    settled = true;
    Object.assign(row, values, { elapsed_seconds: (clock() - began) / 1000 });
    clearTimeout(timer);
    resolve(row);
  };
  const onRequest = request => {
    if (request.method() !== 'POST') return;
    requestTimes.set(request, clock());
    const path = new URL(request.url()).pathname;
    if (path === '/videos/uploads' || path === '/videos/upload') {
      intentStart = clock();
      row.pre_request_seconds = (intentStart - began) / 1000; // Includes browser hashing.
      row.observed_mode = path === '/videos/upload' ? 'api' : 'direct';
      row.failure_stage = row.observed_mode === 'direct' ? 'intent' : 'api_upload';
    } else if (request.url() === storageUrl) {
      row.failure_stage = 'storage';
    } else if (/^\/videos\/uploads\/[^/]+\/complete$/.test(path)) {
      completionStart = clock(); row.failure_stage = 'completion';
    }
  };
  const onResponse = async response => {
    const responseAt = clock();
    try {
      const request = response.request();
      if (request.method() !== 'POST') return;
      const path = new URL(response.url()).pathname;
      const isIntent = path === '/videos/uploads';
      const isAcceptance = path === '/videos/upload' || /^\/videos\/uploads\/[^/]+\/complete$/.test(path);
      // The browser can start S3 transfer before Playwright finishes reading the
      // intent response. Remember all POST start times and wait for that body.
      if (isIntent && response.ok()) intentRead = response.json().then(body => {
        storageUrl = body.upload?.url ? new URL(body.upload.url).href : undefined;
        return body;
      });
      if (!isIntent && !isAcceptance && intentRead) await intentRead;
      if (settled) return;
      if (response.url() === storageUrl) {
        // Fetch resolves at response headers, matching the application's next step.
        row.storage_seconds = (responseAt - requestTimes.get(request)) / 1000;
        if (!response.ok()) finish({ failure_stage: 'storage', http_status: response.status() });
      } else if (isIntent || isAcceptance) {
        if (!response.ok()) return finish({ http_status: response.status() });
        const body = isIntent ? await intentRead : await response.json();
        if (isIntent) {
          row.intent_seconds = (clock() - intentStart) / 1000;
          if (!body.completed) return;
        }
        const accepted = body.completed || body;
        if (!accepted.video_id) return finish({ failure_stage: 'invalid_acceptance_response' });
        if (completionStart !== undefined) row.completion_seconds = (clock() - completionStart) / 1000;
        finish({ outcome: 'accepted', failure_stage: null, video_id: accepted.video_id,
          upload_seconds: (clock() - began) / 1000 });
      }
    } catch { finish({ failure_stage: 'response_read' }); }
  };
  page.on('request', onRequest);
  page.on('response', onResponse);
  timer = setTimeout(() => finish({ outcome: 'timeout' }), timeoutMs);
  try {
    // Do not wait for Playwright's click to finish before starting the deadline.
    Promise.resolve().then(start).catch(() => finish({ failure_stage: 'browser_action' }));
    return await done;
  } finally {
    page.off('request', onRequest);
    page.off('response', onResponse);
  }
}

module.exports = { summarize, measureUpload };
