"""Matched A/B/C/D measurements, retaining failed and censored attempts."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
from statistics import mean, median

from analyze_mixed_experiment import analyze, ledger, quantile, read_rows
from summarize_mixed_comparison import paired_ratio
from pipeline_experiment.architecture_processes import same_pool


STAGES = {'a': ('serialized', 'api'), 'b': ('branches', 'api'),
          'c': ('dag', 'api'), 'd': ('dag', 'direct')}


def verification_rows(folder, reconciliation):
    path = folder/'verification.json'
    if path.exists():
        return json.loads(path.read_text())['verified'], True
    # A fully failed upload trial has no committed objects to verify. Preserve it
    # as a failed workload; any missing proof for actual commits fails integrity.
    return [], not reconciliation['logical_ids']


def reduction(before, after):
    if before <= 0:
        raise ValueError('A positive baseline is required')
    return 100 * (before - after) / before


def analyze_trial(folder, plan):
    spec = json.loads((folder/'spec.json').read_text())
    assert (spec['architecture'], spec['mode']) == STAGES[spec['stage']]
    row = analyze(folder)  # Reuse arrival, query, resource and backlog accounting.
    row.update(stage=spec['stage'], architecture=spec['architecture'], repeat=spec['repeat'], pair_id=spec['pair_id'])
    client = folder/'client'
    sentinel = read_rows(client/'sentinel.jsonl')
    row['sentinel_upload'] = ledger(sentinel, 1, 'accepted')
    readiness_file = client/'sentinel-readiness.json'
    readiness = json.loads(readiness_file.read_text()) if readiness_file.exists() else {'outcome': 'missing'}
    row['readiness'] = readiness
    rows = read_rows(client/'uploads.jsonl') + sentinel
    accepted = [r for r in rows if r['outcome'] == 'accepted']
    ids = [r['video_id'] for r in accepted]
    reconciliation = json.loads((folder/'reconciliation.json').read_text())
    verification, proof_available = verification_rows(folder, reconciliation)
    sentinel_ids = {r['video_id'] for r in sentinel if r['outcome'] == 'accepted'}
    integrity = proof_available and len(ids) == len(set(ids)) == len(verification)
    integrity = integrity and {r['video_id'] for r in verification} == set(ids) == set(reconciliation['logical_ids'])
    integrity = integrity and not reconciliation['missing_commits'] and not reconciliation['unreturned_commits']
    for verified in verification:
        kind = 'sentinel' if verified['video_id'] in sentinel_ids else 'background'
        fixture = plan['fixtures'][Path(spec['sentinel_file'] if kind == 'sentinel' else spec['file']).name]
        integrity = integrity and verified['kind'] == kind and all(verified[k] == fixture[k] for k in ['bytes', 'sha256'])
        integrity = integrity and (verified['job_count'] == 0 if spec['architecture'] == 'serialized' else verified['job_count'] >= 2)
    if readiness.get('outcome') == 'ready':
        matching = [r for r in verification if r['video_id'] == readiness['video_id']]
        integrity = integrity and readiness['video_id'] in sentinel_ids and len(matching) == 1 and matching[0].get('semantic_probe_pass', False)
    row['integrity_pass'] = bool(integrity)
    samples = read_rows(folder/'server-samples.jsonl')
    start = json.loads((client/'go.json').read_text())['start_epoch'] + spec['baseline_seconds']
    loaded = [s for s in samples if start <= s['epoch'] <= start + spec['duration_seconds']]
    if len(loaded) >= 2:
        first, last = loaded[0], loaded[-1]
        elapsed = last['epoch'] - first['epoch']
        before, after = first['containers']['bench-search'], last['containers']['bench-search']
        cpu_seconds = (int(after['cpu']['usage_usec']) - int(before['cpu']['usage_usec'])) / 1e6
        row['worker_resources'] = {'cpu_seconds': cpu_seconds, 'sampled_seconds': elapsed,
            'allocated_cpu_utilization': cpu_seconds / elapsed / (after['cpu_nano'] / 1e9),
            'throttled_seconds': (int(after['cpu'].get('throttled_usec', 0)) - int(before['cpu'].get('throttled_usec', 0))) / 1e6}
    worker = [s['containers']['bench-search'] for s in samples]
    worker_valid = bool(worker) and len({w['id'] for w in worker}) == len({w['started_at'] for w in worker}) == 1
    worker_valid = worker_valid and all(w['running'] and not w['oom_killed'] and w['cpu_nano'] == 4e9 and w['memory_bytes'] == 10*1024**3 for w in worker)
    warm = json.loads((folder/'worker-warmup.json').read_text())
    worker_valid = worker_valid and len(set(warm['pids'])) == 4 and all(
        v['architecture'] == spec['architecture'] and v['chunk_seconds'] == (1800 if spec['stage'] == 'b' else 300)
        for v in warm['configuration'])
    child_stability = bool(warm.get('live_children')) and all(same_pool(warm['live_children'], sample.get('worker_children', [])) for sample in samples)
    row['worker_child_stability_verified'] = child_stability
    if plan['role'] == 'primary':
        worker_valid = worker_valid and child_stability and all(
            s['containers']['bench-search'].get('image_reference') == plan['application_image'] and
            s['containers']['bench-api'].get('image_reference') == plan['application_image'] and
            '--concurrency=4' in s['containers']['bench-search'].get('command', []) and
            'search,playback,celery' in s['containers']['bench-search'].get('command', []) for s in samples)
    row['worker_capacity_valid'] = bool(worker_valid)
    row['arrival_validation_pass'] = bool(row['arrival_validation_pass'] and worker_valid and
        row['sentinel_upload']['complete_unique_ledger'] and sentinel and
        quantile([r['lag_seconds'] for r in sentinel], .99) <= .1)
    row['failed_processing_jobs'] = sum(r['failed_jobs'] for r in verification)
    row['failed_videos'] = sum(r['status'] == 'failed' for r in verification)
    row['publication_verifications'] = sum(r.get('semantic_probe_pass', False) for r in verification)
    row['accepted_total'] = len(ids)
    row['search_slo_pass'] = bool(row['search_slo_pass'] and row['arrival_validation_pass'])
    row['upload_success_pass'] = bool(row['upload_success_pass'] and row['sentinel_upload']['success_fraction'] == 1)
    return row


def compare_groups(left, right, metric, *, seed=20261112, draws=10000):
    assert len(left) == len(right) and left, 'A paired comparison requires all planned repetitions'
    assert [r['pair_id'] for r in left] == [r['pair_id'] for r in right]
    result = {'metric': metric, 'all_planned_pairs_valid': all(
        r['arrival_validation_pass'] and r['integrity_pass'] and r['upload_success_pass']
        and r['query_ledger']['success_fraction'] >= .99
        and r['phases']['load']['success_fraction'] >= .99 for r in left + right)}
    if metric == 'readiness_seconds':
        result['all_ready'] = all(r['readiness'].get('outcome') == 'ready' for r in left + right)
        if not result['all_ready'] or not result['all_planned_pairs_valid']:
            return result  # Never silently remove censored or invalid observations.
        a = [r['readiness']['after_acceptance_seconds'] for r in left]
        b = [r['readiness']['after_acceptance_seconds'] for r in right]
    else:
        if not result['all_planned_pairs_valid']:
            return result
        a = [r['phases']['load']['p95_seconds'] for r in left]
        b = [r['phases']['load']['p95_seconds'] for r in right]
    ratio = (paired_ratio(a, b, seed=seed, draws=draws) if len(a) > 1 else
             {'right_over_left': mean(b)/mean(a), 'independent_pairs': 1,
              'uncertainty': 'Not estimated from a single calibration pair'})
    result.update(before_values_seconds=a, after_values_seconds=b,
                  before_mean_seconds=mean(a), after_mean_seconds=mean(b),
                  before_median_seconds=median(a), after_median_seconds=median(b),
                  mean_reduction_percent=reduction(mean(a), mean(b)),
                  paired_absolute_differences_seconds=[x-y for x,y in zip(a,b)], **ratio)
    return result


def incomplete_trial(folder, spec, progress):
    """Retain started/aborted work even when reconciliation prevented cleanup."""
    client = folder/'client'
    rows = read_rows(client/'uploads.jsonl') + read_rows(client/'sentinel.jsonl')
    queries = read_rows(client/'queries.jsonl')
    def optional(name, default):
        path = folder/name
        return json.loads(path.read_text()) if path.exists() else default
    return {'name': spec['name'], 'rate': spec['uploads_per_minute'], 'stage': spec['stage'],
            'architecture': spec['architecture'], 'mode': spec['mode'], 'repeat': spec['repeat'],
            'pair_id': spec['pair_id'], 'trial_state': 'aborted' if progress.get('failure_type') else 'running',
            'failure': progress.get('failure'), 'arrival_validation_pass': False, 'integrity_pass': False,
            'upload_success_pass': False, 'worker_capacity_valid': False, 'search_slo_pass': False,
            'accepted_total': sum(r.get('outcome') == 'accepted' for r in rows),
            'recorded_upload_attempts': len(rows), 'recorded_queries': len(queries),
            'query_ledger': {'success_fraction': sum(r.get('outcome') == 'success' for r in queries)/len(queries) if queries else 0},
            'readiness': optional('client/sentinel-readiness.json', {'outcome': 'missing'}),
            'reconciliation': optional('reconciliation.json', {'missing_evidence': True}),
            'missing_evidence': [name for name in ['spec.json', 'verification.json', 'reconciliation.json', 'client/client-result.json'] if not (folder/name).exists()]}


def summarize(root, registered, *, partial=False):
    plan = json.loads(registered.read_text())
    assert json.loads((root/'plan.json').read_text()) == plan
    progress = json.loads((root/'progress.json').read_text())
    completed = {r['trial'] for r in progress['completed']}
    if not partial:
        assert progress['finished'] and len(completed) == len(plan['trials']), 'Incomplete campaign'
    rows = []
    for spec in plan['trials']:
        folder = root/spec['name']
        if spec['name'] not in completed:
            if folder.exists() or progress.get('active_trial') == spec['name']:
                rows.append(incomplete_trial(folder, spec, progress))
            continue
        actual = json.loads((folder/'spec.json').read_text())
        assert all(actual.get(k) == v for k,v in spec.items())
        rows.append({**analyze_trial(folder, plan), 'trial_state': 'completed'})
    comparisons = []
    for rate in sorted({r['rate'] for r in rows}):
        groups = {stage: sorted([r for r in rows if r['rate'] == rate and r['stage'] == stage], key=lambda r:r['repeat']) for stage in STAGES}
        for a,b in itertools.combinations(STAGES, 2):
            if len(groups[a]) != plan['analysis']['repetitions'] or len(groups[b]) != plan['analysis']['repetitions']:
                comparisons.append({'rate': rate, 'before': a, 'after': b, 'complete_matched_repetitions': False,
                                    'before_observed': len(groups[a]), 'after_observed': len(groups[b]), 'metrics': []})
                continue
            comparisons.append({'rate': rate, 'before': a, 'after': b, 'adjacent': (a,b) in [('a','b'),('b','c'),('c','d')],
                'metrics': [compare_groups(groups[a], groups[b], metric,
                    seed=plan['analysis']['bootstrap_seed'], draws=plan['analysis']['bootstrap_repetitions'])
                    for metric in ['readiness_seconds', 'search_p95_seconds']]})
    return {'plan_sha256': hashlib.sha256(registered.read_bytes()).hexdigest(), 'partial': partial,
            'progress': progress, 'planned_trials': len(plan['trials']), 'trials': rows, 'comparisons': comparisons,
            'limits': ['Three independent repetitions per primary cell; uncertainty is conditional on this workload.',
                       'A uses a retained legacy implementation; B is the durable DAG with a whole-video search unit.',
                       'Fixed A/B/C/D execution order; earlier experiments are supporting evidence, not pooled samples.',
                       'No sustainable-capacity claim follows from accepting uploads when processing backlog grows.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--registered', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--partial', action='store_true')
    args = parser.parse_args()
    result = summarize(args.root, args.registered, partial=args.partial)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'trials': len(result['trials']), 'comparisons': result['comparisons']}, indent=2))
