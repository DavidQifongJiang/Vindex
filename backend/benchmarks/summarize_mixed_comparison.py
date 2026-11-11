"""Paired offline summaries for a completed, preregistered mixed-load campaign."""
import argparse
import hashlib
import json
from pathlib import Path
import random
from statistics import mean

from analyze_mixed_experiment import analyze, quantile


def paired_ratio(left, right, seed=20261111, draws=10000):
    assert len(left) == len(right) and left and all(x > 0 for x in left+right)
    generator = random.Random(seed)
    ratios = []
    for _ in range(draws):
        indices = [generator.randrange(len(left)) for _ in left]
        ratios.append(mean(right[i] for i in indices)/mean(left[i] for i in indices))
    return {'right_over_left': mean(right)/mean(left), 'paired_95pct_interval': [quantile(ratios, .025), quantile(ratios, .975)],
            'independent_pairs': len(left), 'resamples': draws, 'seed': seed}


def summarize(root, registered):
    plan = json.loads(registered.read_text())
    observed = json.loads((root/'plan.json').read_text())
    assert observed == plan, 'Observed campaign differs from the registered plan'
    progress = json.loads((root/'progress.json').read_text())
    assert progress['finished'] and len(progress['completed']) == len(plan['trials']), 'Incomplete campaign'
    actual_names = {p.parent.name for p in root.glob('*/spec.json')}
    assert actual_names == {t['name'] for t in plan['trials']}, 'Unexpected or missing trials'
    rows = []
    for requested in plan['trials']:
        folder = root/requested['name']; spec = json.loads((folder/'spec.json').read_text())
        assert all(spec[k] == v for k, v in requested.items()), 'Trial setting changed'
        assert spec['commit'] == plan['application_commit'] and spec['seeds'] == plan['protected_seed_ids']
        row = analyze(folder); row['repeat'] = spec['repeat']; row['pair_id'] = spec['pair_id']; rows.append(row)
    cohorts, comparisons = [], []
    for rate in sorted({r['rate'] for r in rows}):
        paired = {}
        for mode in ['api', 'direct']:
            group = sorted([r for r in rows if r['rate'] == rate and r['mode'] == mode], key=lambda r: r['repeat'])
            paired[mode] = group
            assert len(group) == plan['analysis']['repetitions_per_mode_rate']
            cohorts.append({'rate': rate, 'mode': mode, 'trials': len(group),
                'search_p95_mean_seconds': mean(r['phases']['load']['p95_seconds'] for r in group),
                'search_p95_range_seconds': [min(r['phases']['load']['p95_seconds'] for r in group), max(r['phases']['load']['p95_seconds'] for r in group)],
                'baseline_p95_mean_seconds': mean(r['phases']['baseline']['p95_seconds'] for r in group),
                'relative_p95_mean': mean(r['search_p95_baseline_ratio'] for r in group),
                'api_allocated_cpu_mean': mean(r['api_cpu_fraction_of_one_core']/.5 for r in group),
                'uploads_planned': sum(r['uploads']['planned'] for r in group), 'uploads_accepted': sum(r['uploads']['successes'] for r in group),
                'queries_planned': sum(r['query_ledger']['planned'] for r in group), 'queries_correct': sum(r['query_ledger']['successes'] for r in group),
                'last_third_upload_completions_per_minute': [r['uploads']['completed_last_third_per_minute'] for r in group],
                'search_gate_passes': sum(r['search_slo_pass'] for r in group),
                'valid_trials': sum(r['arrival_validation_pass'] for r in group),
                'integrity_passes': sum(r['integrity_pass'] for r in group),
                'all_gates_pass': all(r['arrival_validation_pass'] and r['search_slo_pass'] and r['upload_success_pass'] and r['integrity_pass'] for r in group)})
        api, direct = paired['api'], paired['direct']
        assert [r['pair_id'] for r in api] == [r['pair_id'] for r in direct]
        comparable = all(r['arrival_validation_pass'] and r['integrity_pass'] for r in api+direct)
        comparison = {'rate': rate, 'all_planned_pairs_valid': comparable}
        if comparable:
            comparison['search_p95_ratio'] = paired_ratio([r['phases']['load']['p95_seconds'] for r in api], [r['phases']['load']['p95_seconds'] for r in direct],
                seed=plan['analysis']['bootstrap_seed'], draws=plan['analysis']['bootstrap_repetitions'])
            comparison['api_cpu_ratio'] = paired_ratio([r['api_cpu_fraction_of_one_core'] for r in api], [r['api_cpu_fraction_of_one_core'] for r in direct],
                seed=plan['analysis']['bootstrap_seed'], draws=plan['analysis']['bootstrap_repetitions'])
        comparisons.append(comparison)
    return {'registered_plan_sha256': hashlib.sha256(registered.read_bytes()).hexdigest(), 'started_at': progress['started_at'],
            'finished_at': progress['finished_at'], 'cohorts': cohorts, 'paired_comparisons': comparisons, 'trials': rows,
            'limits': ['Three whole-trial pairs per rate; intervals are conditional on this controlled workload.',
                       'Workers paused: admission/search interference only, not processing throughput.',
                       'Highest passing tested rate is a lower bound; no unmeasured capacity maximum is inferred.']}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('root', type=Path); parser.add_argument('--registered', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); value = summarize(args.root, args.registered)
    args.output.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'cohorts': value['cohorts'], 'paired_comparisons': value['paired_comparisons']}, indent=2))


if __name__ == '__main__': main()
