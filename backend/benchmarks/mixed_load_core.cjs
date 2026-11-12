// Shared clock/scheduling rules for the isolated mixed-load experiment.
const { performance } = require('node:perf_hooks');
const sleep = ms => new Promise(resolve => setTimeout(resolve, Math.max(0, ms)));

function arrivalOffsets(ratePerSecond, durationSeconds) {
  if (!Number.isFinite(ratePerSecond) || ratePerSecond < 0 || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    throw new Error('Invalid arrival schedule');
  }
  if (ratePerSecond === 0) return [];
  const count = Math.ceil(ratePerSecond * durationSeconds - 1e-9);
  return Array.from({ length: count }, (_, i) => i / ratePerSecond);
}

function phaseAt(seconds, spec) {
  if (seconds < spec.baseline_seconds) return 'baseline';
  if (seconds < spec.baseline_seconds + spec.duration_seconds) return 'load';
  return 'recovery';
}

function validateSpec(s) {
  if (s.environment !== 'staging' || !/^[a-z0-9-]+$/.test(s.name) || !['api', 'direct'].includes(s.mode)) throw new Error('Invalid experiment identity');
  if (s.origin !== 'https://bench-proxy:8443') throw new Error('Only the isolated proxy is allowed');
  if (!/^[a-f0-9]{40}$/.test(s.commit)) throw new Error('Pin the application commit');
  for (const [key, min, max] of [['duration_seconds', 30, 600], ['baseline_seconds', 30, 120],
    ['recovery_seconds', 30, 120], ['pool_size', 1, 24], ['upload_timeout_seconds', 30, 300]]) {
    if (!Number.isInteger(s[key]) || s[key] < min || s[key] > max) throw new Error(`Invalid ${key}`);
  }
  if (!Number.isFinite(s.uploads_per_minute) || s.uploads_per_minute < 0 || s.uploads_per_minute > 120) throw new Error('Invalid upload rate');
  if (!Number.isFinite(s.search_rps) || s.search_rps <= 0 || s.search_rps > 10) throw new Error('Invalid search rate');
  if (!Array.isArray(s.seeds) || s.seeds.length < 1 || s.seeds.some(id => !/^[a-f0-9-]{36}$/.test(id))) throw new Error('Missing processed search corpus');
  if (!Array.isArray(s.queries) || !s.queries.length || s.queries.some(q => typeof q !== 'string' || q.length > 200)) throw new Error('Invalid queries');
  if (!s.file?.startsWith('/bench/fixtures/') || s.file.includes('..')) throw new Error('Invalid fixture path');
}

// Work is launched at absolute offsets; slow requests never reduce scheduled arrivals.
async function runSchedule(offsets, launch, { clock = () => performance.now() / 1000, pause = sleep, start = clock() } = {}) {
  const pending = [];
  for (let index = 0; index < offsets.length; index++) {
    await pause((start + offsets[index] - clock()) * 1000);
    const lag = Math.max(0, clock() - start - offsets[index]);
    pending.push(Promise.resolve(launch({ index, scheduled_seconds: offsets[index], lag_seconds: lag })));
  }
  return Promise.all(pending);
}

function confirmedNegativeStart(event, previous = null) {
  // A server/transport failure says nothing about readiness at that instant.
  return event.milestone === 'searchable' && !event.ready &&
    event.last_code === 'expected_hit_missing' && Number.isFinite(event.last_probe_started_seconds)
    ? event.last_probe_started_seconds : previous;
}

module.exports = { arrivalOffsets, phaseAt, validateSpec, runSchedule, sleep, confirmedNegativeStart };
