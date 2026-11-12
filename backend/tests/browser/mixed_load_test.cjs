const test = require('node:test');
const assert = require('node:assert/strict');
const { arrivalOffsets, phaseAt, runSchedule, confirmedNegativeStart } = require('../../benchmarks/mixed_load_core.cjs');

test('arrival schedule excludes the endpoint and supports fractional rates', () => {
  assert.deepEqual(arrivalOffsets(2, 2), [0, .5, 1, 1.5]);
  assert.deepEqual(arrivalOffsets(1 / 30, 120), [0, 30, 60, 90]);
  assert.deepEqual(arrivalOffsets(0, 120), []);
  assert.throws(() => arrivalOffsets(-1, 20));
});
test('slow unfinished requests do not suppress later arrivals', async () => {
  let now = 0; const starts = [], finishes = [];
  const job = runSchedule([0, 1, 2], row => {
    starts.push(row.scheduled_seconds);
    return new Promise(resolve => finishes.push(resolve));
  }, { clock: () => now, pause: async ms => { now += Math.max(0, ms) / 1000; }, start: 0 });
  for (let i = 0; i < 6; i++) await Promise.resolve();
  assert.deepEqual(starts, [0, 1, 2]);
  finishes.forEach(resolve => resolve()); await job;
});
test('recovery and baseline records cannot be counted as loaded search samples', () => {
  const spec = { baseline_seconds: 60, duration_seconds: 180 };
  assert.equal(phaseAt(59.9, spec), 'baseline');
  assert.equal(phaseAt(60, spec), 'load');
  assert.equal(phaseAt(240, spec), 'recovery');
});

test('inconclusive probes do not advance the confirmed negative readiness bound', () => {
  let bound = confirmedNegativeStart({ milestone: 'searchable', ready: false,
    last_code: 'expected_hit_missing', last_probe_started_seconds: 10, last_probe_finished_seconds: 11 });
  assert.equal(bound, 10);
  for (const code of ['probe_timeout', 'http_503', 'http_400', 'expected_search_hit']) {
    bound = confirmedNegativeStart({ milestone: 'searchable', ready: code === 'expected_search_hit',
      last_code: code, last_probe_started_seconds: 20, last_probe_finished_seconds: 21 }, bound);
    assert.equal(bound, 10);
  }
});
