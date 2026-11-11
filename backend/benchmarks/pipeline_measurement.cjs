// Opt-in integrated pilot. All milestones share the clock immediately before Upload.
const fs = require('node:fs');
const crypto = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { measureUpload } = require('./upload_measurement.cjs');

function validatePipeline(manifest) {
  const p = manifest.pipeline;
  if (!p) return null;
  if (manifest.benchmark_control) throw new Error('Upload-only control cleanup cannot run with processing workers');
  if (manifest.repetitions !== 1 || manifest.files.length !== 1 || manifest.concurrency > 5) {
    throw new Error('Integrated pilots run one fixture and one wave, with at most five clients');
  }
  if (!['api', 'direct'].includes(p.mode) || !['cold', 'warm'].includes(p.worker_state)) throw new Error('Declare pilot mode and worker state');
  const tlsMode = p.tls_mode || 'verified';
  if (!['verified', 'self_signed_isolated'].includes(tlsMode)) throw new Error('Invalid pilot TLS mode');
  if (!/^[a-f0-9]{40}$/.test(manifest.commit)) throw new Error('Pin the measured application commit');
  for (const [key, min, max] of [['deadline_seconds', 1, 900], ['poll_interval_ms', 100, 10000], ['probe_timeout_ms', 100, 30000]]) {
    if (!Number.isInteger(p[key]) || p[key] < min || p[key] > max) throw new Error(`Invalid pipeline ${key}`);
  }
  const search = p.search;
  if (!search || ![search.query, search.expected_text].every(s => typeof s === 'string' && s.trim().length >= 3 && s.length <= 500) ||
      !Number.isFinite(search.start_seconds) || search.start_seconds < 0 ||
      !Number.isFinite(search.end_seconds) || search.end_seconds <= search.start_seconds ||
      !Number.isFinite(search.tolerance_seconds) || search.tolerance_seconds < 0 || search.tolerance_seconds > 5) {
    throw new Error('Supply a speech query, expected phrase, and annotated time interval');
  }
  const raw = fs.readFileSync(p.preflight_file);
  const preflight = JSON.parse(raw);
  // This is an operator record, not independent infrastructure verification.
  if (preflight.isolated !== true || preflight.queue_empty !== true || preflight.commit !== manifest.commit ||
      preflight.worker_state !== p.worker_state || preflight.target !== manifest.targets[p.mode] ||
      !['media_bucket', 'database', 'redis', 'qdrant', 'worker_identity', 'capacity', 'model_versions', 'cleanup_plan'].every(k =>
        typeof preflight[k] === 'string' && preflight[k].trim())) {
    throw new Error('Complete the isolated worker preflight record before running a pilot');
  }
  if (tlsMode === 'self_signed_isolated' && preflight.tls_mode !== tlsMode) throw new Error('Self-signed TLS requires matching isolated preflight');
  const age = Date.now() - Date.parse(preflight.observed_at);
  if (!Number.isFinite(age) || age < 0 || age > 15 * 60 * 1000) throw new Error('Preflight must have been observed within 15 minutes');
  return { mode: p.mode, worker_state: p.worker_state, tls_mode: tlsMode, deadline_seconds: p.deadline_seconds,
    poll_interval_ms: p.poll_interval_ms, probe_timeout_ms: p.probe_timeout_ms,
    search: Object.fromEntries(['query', 'expected_text', 'start_seconds', 'end_seconds', 'tolerance_seconds'].map(k => [k, search[k]])),
    preflight_sha256: crypto.createHash('sha256').update(raw).digest('hex'),
    preflight_status: 'operator-declared; independently verify before interpreting results' };
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function measurePipeline(page, { start, uploadTimeoutMs, config, probes, onEvent = () => {},
  clock = () => performance.now(), sleep = pause, upload = measureUpload }) {
  const began = clock(), deadline = began + config.deadline_seconds * 1000;
  const row = await upload(page, { start, began, clock, timeoutMs: Math.min(uploadTimeoutMs, deadline - began) });
  // Journal acceptance before waiting on workers so a killed runner retains video identity.
  onEvent({ milestone: 'upload', ...row });
  if (row.outcome !== 'accepted') return { ...row, readiness_outcome: 'not_started' };

  async function observe(name) {
    const result = { outcome: 'timeout', observed_seconds: null, after_acceptance_seconds: null,
      probes: 0, last_code: null, last_probe_started_seconds: null, last_probe_finished_seconds: null };
    while (clock() < deadline) {
      const started = clock();
      const budgetMs = Math.min(config.probe_timeout_ms, deadline - started);
      let timer, sample;
      try {
        sample = await Promise.race([
          probes[name]({ videoId: row.video_id, timeoutMs: budgetMs, search: config.search }),
          // Allow browser abort/media cleanup to report before the driver watchdog.
          new Promise(resolve => { timer = setTimeout(() => resolve({ ready: false, terminal: true, code: 'probe_hung' }), budgetMs + 250); })
        ]);
      } catch { sample = { ready: false, terminal: true, code: 'probe_exception' }; }
      finally { clearTimeout(timer); }
      const ended = clock();
      result.probes++;
      Object.assign(result, { last_code: sample.code, last_probe_started_seconds: (started - began) / 1000,
        last_probe_finished_seconds: (ended - began) / 1000 });
      // A successful response arriving after the deadline must not pass.
      if (ended <= deadline && sample.ready === true) {
        Object.assign(result, { outcome: 'ready', observed_seconds: (ended - began) / 1000,
          after_acceptance_seconds: (ended - began) / 1000 - row.upload_seconds });
      } else if (ended < deadline && sample.terminal) result.outcome = 'failed';
      onEvent({ milestone: name, video_id: row.video_id, ...result, ready: result.outcome === 'ready' });
      if (ended >= deadline || result.outcome !== 'timeout') break;
      await sleep(Math.min(config.poll_interval_ms, Math.max(0, deadline - clock())));
    }
    return result;
  }
  // Neither branch waits for the other; playback may precede search or vice versa.
  const [searchable, playable] = await Promise.all([observe('searchable'), observe('playable')]);
  return { ...row, searchable, playable, readiness_outcome:
    searchable.outcome === 'ready' && playable.outcome === 'ready' ? 'ready' : 'incomplete',
    pipeline_elapsed_seconds: (clock() - began) / 1000 };
}

function summarizePipeline(rows) {
  const summarize = key => {
    const times = rows.filter(r => r.outcome === 'accepted' && r[key]?.outcome === 'ready').map(r => r[key].observed_seconds).sort((a, b) => a - b);
    return { ready: times.length, not_ready: rows.length - times.length,
      observed_ready_fraction: rows.length ? times.length / rows.length : null,
      ready_p50_seconds: times.length ? times[Math.ceil(times.length * 0.5) - 1] : null,
      ready_p95_seconds: times.length ? times[Math.ceil(times.length * 0.95) - 1] : null };
  };
  return { attempted: rows.length, both_ready: rows.filter(r => r.outcome === 'accepted' && r.readiness_outcome === 'ready').length,
    searchable: summarize('searchable'), playable: summarize('playable'),
    interpretation: 'Exploratory single-wave pilot; successful-only quantiles, no population reliability or comparative performance claim' };
}

module.exports = { validatePipeline, measurePipeline, summarizePipeline };
