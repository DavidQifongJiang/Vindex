const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { measurePipeline, summarizePipeline, validatePipeline } = require('../../benchmarks/pipeline_measurement.cjs');

const config = { deadline_seconds: 1, poll_interval_ms: 1, probe_timeout_ms: 500 };
const accepted = { outcome: 'accepted', video_id: 'video-1', upload_seconds: 0.2 };
const ready = async () => ({ ready: true, code: 'verified' });

test('one clock includes upload; independent branches retain playback while search waits', async () => {
  let time = 1000, release;
  const search = new Promise(resolve => { release = resolve; });
  const events = [];
  const pending = measurePipeline(null, { config, clock: () => time,
    upload: async (_, args) => { assert.equal(args.began, 1000); time = 1200; return accepted; },
    probes: { searchable: () => search, playable: async () => { time = 1300; return ready(); } },
    onEvent: event => events.push(event) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(events[0].milestone, 'upload');
  assert.equal(events.find(e => e.milestone === 'playable').observed_seconds, 0.3);
  time = 1800; release({ ready: true, code: 'verified' });
  const result = await pending;
  assert.equal(result.readiness_outcome, 'ready');
  assert.equal(result.searchable.observed_seconds, 0.8);
  assert.ok(Math.abs(result.searchable.after_acceptance_seconds - 0.6) < 1e-8);
  assert.equal(result.playable.observed_seconds, 0.3);
});

test('transient failure retries; successful response past the overall deadline is censored', async () => {
  let time = 0, calls = 0;
  const result = await measurePipeline(null, { config, clock: () => time, upload: async () => accepted,
    sleep: async ms => { time += ms; }, probes: { playable: ready, searchable: async () => {
      calls++;
      if (calls === 1) return { ready: false, code: 'http_503' };
      time = 1001; return { ready: true, code: 'verified' };
    } } });
  assert.equal(calls, 2);
  assert.equal(result.searchable.outcome, 'timeout');
  assert.equal(result.searchable.observed_seconds, null);
  assert.equal(result.playable.outcome, 'ready');
  assert.equal(result.readiness_outcome, 'incomplete');
});

test('upload failure starts no probes and remains in both readiness denominators', async () => {
  const failed = await measurePipeline(null, { config, upload: async () => ({ outcome: 'timeout' }),
    probes: { searchable: () => assert.fail('must not probe'), playable: () => assert.fail('must not probe') } });
  assert.equal(failed.readiness_outcome, 'not_started');
  const summary = summarizePipeline([failed, { outcome: 'accepted', readiness_outcome: 'incomplete',
    searchable: { outcome: 'ready', observed_seconds: 5 }, playable: { outcome: 'failed' } }]);
  assert.equal(summary.attempted, 2);
  assert.equal(summary.both_ready, 0);
  assert.equal(summary.searchable.observed_ready_fraction, 0.5);
  assert.equal(summary.playable.not_ready, 2);
  assert.equal(summary.playable.ready_p95_seconds, null);
  const wrongMode = summarizePipeline([{ outcome: 'failed', failure_stage: 'wrong_frontend_upload_mode',
    readiness_outcome: 'ready', searchable: { outcome: 'ready', observed_seconds: 3 }, playable: { outcome: 'ready', observed_seconds: 2 } }]);
  assert.equal(wrongMode.both_ready, 0);
  assert.equal(wrongMode.searchable.ready, 0);
});

test('a hung probe is bounded and does not erase the other successful branch', async () => {
  const result = await measurePipeline(null, { config: { ...config, probe_timeout_ms: 5 },
    upload: async () => accepted, probes: { searchable: () => new Promise(() => {}), playable: ready } });
  assert.equal(result.searchable.outcome, 'failed');
  assert.equal(result.searchable.last_code, 'probe_hung');
  assert.equal(result.playable.outcome, 'ready');
});

test('terminal authentication failure is retained separately from a readiness timeout', async () => {
  const result = await measurePipeline(null, { config, upload: async () => accepted,
    probes: { searchable: async () => ({ ready: false, terminal: true, code: 'http_401' }), playable: ready } });
  assert.equal(result.searchable.probes, 1);
  assert.equal(result.searchable.outcome, 'failed');
  assert.equal(result.outcome, 'accepted');
});

test('pilot validation bounds cost and rejects upload-only cleanup, stale or mismatched preflight', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vindex-pipeline-test-'));
  try {
    const file = path.join(dir, 'preflight.json');
    const manifest = { commit: 'a'.repeat(40), files: ['speech.mp4'], repetitions: 1, concurrency: 1,
      targets: { api: 'https://fixture.invalid/api', direct: 'https://fixture.invalid/direct' },
      pipeline: { mode: 'direct', worker_state: 'warm', ...config, poll_interval_ms: 100,
        preflight_file: file, search: { query: 'vector search', expected_text: 'vector search',
          start_seconds: 1, end_seconds: 3, tolerance_seconds: 1 } } };
    const preflight = { isolated: true, queue_empty: true, commit: manifest.commit, worker_state: 'warm',
      target: manifest.targets.direct, observed_at: new Date().toISOString(),
      ...Object.fromEntries(['media_bucket', 'database', 'redis', 'qdrant', 'worker_identity', 'capacity', 'model_versions', 'cleanup_plan'].map(k => [k, 'fixture'])) };
    fs.writeFileSync(file, JSON.stringify(preflight));
    assert.equal(validatePipeline(manifest).preflight_sha256.length, 64);
    assert.throws(() => validatePipeline({ ...manifest, benchmark_control: {} }), /cleanup/);
    assert.throws(() => validatePipeline({ ...manifest, repetitions: 2 }), /one wave/);
    assert.throws(() => validatePipeline({ ...manifest, concurrency: 6 }), /five clients/);
    assert.throws(() => validatePipeline({ ...manifest, pipeline: { ...manifest.pipeline, deadline_seconds: 901 } }), /deadline/);
    fs.writeFileSync(file, JSON.stringify({ ...preflight, worker_state: 'cold' }));
    assert.throws(() => validatePipeline(manifest), /preflight/);
    fs.writeFileSync(file, JSON.stringify({ ...preflight, observed_at: '2000-01-01T00:00:00Z' }));
    assert.throws(() => validatePipeline(manifest), /15 minutes/);
    assert.equal(validatePipeline({}), null);
  } finally { fs.rmSync(dir, { recursive: true }); }
});
