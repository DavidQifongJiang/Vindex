"""Paired-wave analysis of completed upload comparisons; no service access."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def quantile(values, q, axis=None):
    return np.quantile(values, q, axis=axis, method='inverted_cdf')


def paired_bootstrap(api, direct, *, iterations=10000, seed=20261109):
    """Resample matching waves, retaining every client in the sampled wave."""
    a, b = np.asarray(api, dtype=float), np.asarray(direct, dtype=float)
    if a.ndim != 2 or a.shape != b.shape or not a.size or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Require matching finite wave-by-client matrices')
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, a.shape[0], size=(iterations, a.shape[0]))
    sampled_a = a[indices].reshape(iterations, -1)
    sampled_b = b[indices].reshape(iterations, -1)
    result = {}
    for name, q in [('p50', .5), ('p95', .95)]:
        qa, qb = quantile(sampled_a, q, axis=1), quantile(sampled_b, q, axis=1)
        result[name] = {'api_seconds': float(quantile(a, q)), 'direct_seconds': float(quantile(b, q)),
                        'api_seconds_95pct_wave_bootstrap': quantile(qa, [.025, .975]).tolist(),
                        'direct_seconds_95pct_wave_bootstrap': quantile(qb, [.025, .975]).tolist(),
                        'direct_over_api': float(quantile(b, q) / quantile(a, q)),
                        'ratio_95pct_paired_wave_bootstrap': quantile(qb / qa, [.025, .975]).tolist(),
                        'difference_seconds_95pct_paired_wave_bootstrap': quantile(qb - qa, [.025, .975]).tolist()}
    return result


def mean_ratio_interval(api, direct, *, iterations=10000, seed=20261109):
    a, b = np.asarray(api, dtype=float), np.asarray(direct, dtype=float)
    if a.shape != b.shape or a.ndim != 1 or not len(a) or a.sum() <= 0:
        raise ValueError('Require matching wave totals and positive baseline')
    indices = np.random.default_rng(seed).integers(0, len(a), size=(iterations, len(a)))
    ratios = b[indices].sum(axis=1) / a[indices].sum(axis=1)
    return {'direct_over_api': float(b.sum() / a.sum()), 'ratio_95pct_paired_wave_bootstrap': quantile(ratios, [.025, .975]).tolist()}


def wilson(successes, attempts):
    if not attempts:
        return None
    z = 1.959963984540054
    p, d = successes / attempts, 1 + z * z / attempts
    center = (p + z*z/(2*attempts)) / d
    margin = z * np.sqrt(p*(1-p)/attempts + z*z/(4*attempts*attempts)) / d
    return [max(0, float(center-margin)), min(1, float(center+margin))]


def analyze_cell(directory, *, iterations=10000):
    run = json.loads((directory / 'run.json').read_text())
    completion = json.loads((directory / 'completion.json').read_text())
    rows, resources = read_jsonl(directory/'attempts.jsonl'), read_jsonl(directory/'resources.jsonl')
    verification = read_jsonl(directory/'verification.jsonl')
    if not completion['finished_all_planned_waves'] or len(rows) != completion['planned_attempts']:
        raise ValueError('Incomplete cell cannot be analyzed as a completed comparison')
    if len(run['files']) != 1:
        raise ValueError('Analyze one file/network/concurrency cohort per cell')
    expected = run['repetitions'] * 2
    if len(resources) != expected or len(verification) != expected:
        raise ValueError('Missing wave resources or verification')
    result = {'cell': directory.name, 'file': run['files'][0], 'concurrency': run['concurrency'],
              'paired_waves': run['repetitions'], 'network': run['network'], 'modes': {}, 'gates': {}}
    result['server'] = json.loads((directory/'server.json').read_text())
    grouped, wave_resources = {}, {}
    for mode in ['api', 'direct']:
        selected = [r for r in rows if r['mode'] == mode]
        accepted = [r for r in selected if r['outcome'] == 'accepted']
        if len(selected) != run['concurrency'] * run['repetitions']:
            raise ValueError('Mode attempt count differs from plan')
        ordered = sorted(selected, key=lambda r: (r['repetition'], r['client']))
        if [(r['repetition'], r['client']) for r in ordered] != [(w, c) for w in range(run['repetitions']) for c in range(run['concurrency'])]:
            raise ValueError('Missing or duplicate wave/client identities')
        measured = sorted([r for r in resources if r['mode'] == mode], key=lambda r: r['repetition'])
        if [r['repetition'] for r in measured] != list(range(run['repetitions'])):
            raise ValueError('Missing or duplicate resource wave identity')
        confirmed = [v for v in verification if v['mode'] == mode]
        if len(confirmed) != run['repetitions'] or any(v['remaining_test_objects'] or v['remaining_videos'] or v['remaining_jobs'] for v in confirmed):
            raise ValueError('Cleanup evidence incomplete')
        verified_ids = [v['video_id'] for w in confirmed for v in w['verified']]
        if len(verified_ids) != len(set(verified_ids)) or set(verified_ids) != {v['video_id'] for v in accepted}:
            raise ValueError('Integrity evidence does not match accepted attempts')
        if any(not v['sha256_verified'] or v['initial_jobs'] != 2 for w in confirmed for v in w['verified']):
            raise ValueError('Integrity or initial job verification failed')
        accepted_gib = sum(r['bytes'] for r in accepted) / 2**30
        totals = {key: sum(w[key] for w in measured) for key in ['cpu_seconds', 'write_bytes', 'cancelled_write_bytes',
            'write_syscall_bytes', 'network_rx_bytes', 'network_tx_bytes', 'api_request_body_bytes', 'api_response_body_bytes', 'throttled_seconds']}
        totals['proxy_cpu_seconds'] = sum(w.get('proxy_cpu_seconds', 0) for w in measured)
        totals['combined_cpu_seconds'] = totals['cpu_seconds'] + totals['proxy_cpu_seconds']
        result['modes'][mode] = {'attempted': len(selected), 'accepted': len(accepted), 'failed': len(selected)-len(accepted),
            'acceptance_rate': len(accepted)/len(selected), 'acceptance_95pct_wilson_descriptive': wilson(len(accepted),len(selected)),
            'accepted_gib': accepted_gib, 'resource_totals': totals,
            'cpu_seconds_per_accepted_gib': totals['cpu_seconds']/accepted_gib if accepted_gib else None,
            'combined_cpu_seconds_per_accepted_gib': totals['combined_cpu_seconds']/accepted_gib if accepted_gib else None,
            'median_wave_peak_rss_mib': float(np.median([w['rss_peak_bytes']/2**20 for w in measured])),
            'median_wave_container_memory_increase_mib': float(np.median([(w['container_memory_peak_bytes']-w['container_memory_start_bytes'])/2**20 for w in measured])),
            'mean_pre_request_seconds': float(np.mean([r['pre_request_seconds'] for r in accepted])) if accepted else None,
            'failed_attempts': [r for r in selected if r['outcome'] != 'accepted']}
        grouped[mode] = ordered
        wave_resources[mode] = measured
    all_accepted = all(v['accepted'] == v['attempted'] for v in result['modes'].values())
    result['gates']['accepted_input_and_initial_jobs'] = 'passed' if all_accepted else 'failed'
    result['gates']['per_wave_cleanup'] = 'passed'
    result['gates']['observed_acceptance_at_least_99_percent'] = 'passed' if all(v['acceptance_rate'] >= .99 for v in result['modes'].values()) else 'failed'
    result['gates']['population_reliability_99_percent'] = 'unverified: descriptive intervals do not establish independent population reliability'
    if all_accepted:
        matrices = {m: np.array([r['upload_seconds'] for r in grouped[m]]).reshape(run['repetitions'],run['concurrency']) for m in grouped}
        result['latency'] = paired_bootstrap(matrices['api'],matrices['direct'],iterations=iterations)
        low, high = result['latency']['p95']['ratio_95pct_paired_wave_bootstrap']
        enough = all(v['accepted'] >= 100 for v in result['modes'].values())
        result['gates']['p95_at_most_10_percent_regression'] = ('passed' if high <= 1.10 else 'failed' if low > 1.10 else 'inconclusive') if enough else 'unverified: exploratory sample'
    else:
        result['gates']['p95_at_most_10_percent_regression'] = 'unverified: failures retained, no complete paired latency matrix'
    result['resource_ratios'] = {}
    for key in ['cpu_seconds','write_bytes','network_rx_bytes','network_tx_bytes','api_request_body_bytes']:
        api = [w[key] for w in wave_resources['api']]
        direct = [w[key] for w in wave_resources['direct']]
        if sum(api):
            result['resource_ratios'][key] = mean_ratio_interval(api,direct,iterations=iterations)
    result['resource_ratios']['combined_cpu_seconds'] = mean_ratio_interval(
        [w['cpu_seconds'] + w.get('proxy_cpu_seconds', 0) for w in wave_resources['api']],
        [w['cpu_seconds'] + w.get('proxy_cpu_seconds', 0) for w in wave_resources['direct']], iterations=iterations)
    for key, threshold, name in [('api_request_body_bytes',.01,'api_body_traffic_reduction_99_percent'),('write_bytes',.05,'storage_write_accounting_reduction_95_percent')]:
        ratio = result['resource_ratios'].get(key)
        result['gates'][name] = 'passed' if ratio and ratio['ratio_95pct_paired_wave_bootstrap'][1] <= threshold else 'inconclusive'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    campaign = json.loads((args.campaign/'campaign.json').read_text())
    if not campaign['completed']:
        raise SystemExit('Campaign is incomplete; preserve partial evidence')
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'method': '10,000 paired-wave bootstrap resamples; seed 20261109; nearest-rank quantiles',
              'cells': [analyze_cell(args.campaign/c['name']) for c in campaign['cells']],
              'limitations': ['One temporary AWS host and browser client machine; configured network caps share one physical uplink.',
                  'API upload path and direct path use the same deployed commit; this is not a historical production A/B.',
                  'Processing, authentication-provider/edge overhead, broad internet conditions and monthly billing are not compared.',
                  'write_bytes includes cancelled dirty-page writes; it is not physical-device I/O or a billed storage amount.',
                  'Reliability intervals are descriptive; concurrent attempts and repeated files are not independent population trials.']}
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(args.output),'cells':len(report['cells'])}))


if __name__ == '__main__':
    main()
