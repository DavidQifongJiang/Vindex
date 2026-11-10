const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { summarize, measureUpload } = require('../../benchmarks/upload_measurement.cjs');
const request = url => ({ method: () => 'POST', url: () => url });
const response = (req, body, status = 200) => ({ request: () => req, url: req.url,
  ok: () => status >= 200 && status < 300, status: () => status, json: async () => body });

test('failure and timeout remain in the acceptance denominator; quantiles use successes only', () => {
  const summary = summarize([{ outcome: 'accepted', upload_seconds: 2 },
    { outcome: 'failed' }, { outcome: 'timeout' }, { outcome: 'accepted', upload_seconds: 4 }]);
  assert.equal(summary.attempted, 4);
  assert.equal(summary.acceptance_rate, 0.5);
  assert.equal(summary.accepted_p95_seconds, 4);
  assert.ok(summary.acceptance_rate_95pct_wilson[0] < 0.5);
  assert.ok(summary.acceptance_rate_95pct_wilson[1] > 0.5);
  assert.ok(summary.tail_sample_warning);
  assert.equal(summarize([]).accepted_p95_seconds, null);
});

test('one perfect attempt does not establish a 99 percent reliability claim', () => {
  assert.ok(summarize([{ outcome: 'accepted', upload_seconds: 1 }]).acceptance_rate_95pct_wilson[0] < 0.99);
});

test('direct timing includes pre-request hashing and waits for durable acceptance', async () => {
  const page = new EventEmitter();
  let time = 0;
  const result = await measureUpload(page, { timeoutMs: 1000, clock: () => time, start: async () => {
    time = 500;
    const intent = request('https://app.test/videos/uploads');
    page.emit('request', intent);
    const stored = request('https://bucket.test/');
    page.emit('response', response(intent, { upload: { url: stored.url() } }));
    time = 600; page.emit('request', stored); // Intent body may still be pending.
    time = 800; page.emit('response', response(stored, null, 204));
    await new Promise(resolve => setImmediate(resolve));
    const complete = request('https://app.test/videos/uploads/video-1/complete');
    time = 900; page.emit('request', complete);
    time = 1000; page.emit('response', response(complete, { video_id: 'video-1' }));
  } });
  assert.equal(result.outcome, 'accepted');
  assert.equal(result.pre_request_seconds, 0.5);
  assert.equal(result.upload_seconds, 1);
  assert.ok(Math.abs(result.storage_seconds - 0.2) < 0.00001);
  assert.equal(page.listenerCount('response'), 0);
});

test('HTTP errors and a browser that never responds are preserved as failed attempts', async () => {
  const page = new EventEmitter();
  const error = await measureUpload(page, { timeoutMs: 1000, start: () => {
    const req = request('https://app.test/videos/upload');
    page.emit('request', req); page.emit('response', response(req, {}, 503));
  } });
  assert.equal(error.outcome, 'failed');
  assert.equal(error.http_status, 503);
  const timeout = await measureUpload(page, { timeoutMs: 5, start: () => {} });
  assert.equal(timeout.outcome, 'timeout');
  assert.equal(timeout.upload_seconds, null);
});
