"""Report the registered worker-active confirmation without claiming steady state."""
import argparse
import hashlib
import json
from pathlib import Path

from analyze_mixed_experiment import analyze, read_rows


def pipeline_window(samples, begin, end):
    observed = [row for row in samples if begin <= row['epoch'] <= end]
    assert len(observed) >= 2, 'Insufficient observations for a completion-rate estimate'
    first, last = observed[0], observed[-1]
    elapsed = last['epoch']-first['epoch']
    assert elapsed > 0
    return {'observed_seconds': elapsed, 'first_offset_seconds': first['epoch']-begin,
            'last_offset_seconds': last['epoch']-begin,
            'first': first['pipeline'], 'last': last['pipeline'],
            'searchable_completions_per_minute': (last['pipeline']['searchable']-first['pipeline']['searchable'])*60/elapsed,
            'playable_completions_per_minute': (last['pipeline']['playable']-first['pipeline']['playable'])*60/elapsed,
            'pending_growth': last['pipeline']['pending_videos']-first['pipeline']['pending_videos']}


def summarize(root, registered):
    plan = json.loads(registered.read_text())
    assert json.loads((root/'plan.json').read_text()) == plan, 'Campaign differs from registration'
    progress = json.loads((root/'progress.json').read_text())
    assert progress['finished'] and not progress.get('failure'), 'Incomplete campaign'
    assert [row['trial'] for row in progress['completed']] == [row['name'] for row in plan['trials']], 'Missing or reordered trials'
    assert {p.parent.name for p in root.glob('*/spec.json')} == {row['name'] for row in plan['trials']}
    trials = []
    for requested in plan['trials']:
        folder = root/requested['name']
        spec = json.loads((folder/'spec.json').read_text())
        assert all(spec[key] == value for key, value in requested.items()), 'Trial differs from registration'
        assert spec['processing'] == 'active' and spec['commit'] == plan['application_commit']
        assert spec['seeds'] == plan['protected_seed_ids']
        report = analyze(folder)
        begin = json.loads((folder/'client/go.json').read_text())['start_epoch']+spec['baseline_seconds']
        samples = read_rows(folder/'server-samples.jsonl')
        worker_checks = {}
        for worker, prefix in [('bench-search', 'search'), ('bench-playback', 'playback')]:
            observed = [row['containers'][worker] for row in samples]
            worker_checks[worker] = bool(observed and len({row['id'] for row in observed}) == 1 and
                len({row['started_at'] for row in observed}) == 1 and
                all(row['running'] and not row['oom_killed'] and
                    row['cpu_nano'] == plan['worker_capacity'][prefix+'_vcpu']*1e9 and
                    row['memory_bytes'] == plan['worker_capacity'][prefix+'_memory_gib']*1024**3 for row in observed))
        report['worker_capacity_and_stability'] = worker_checks
        report['processing_windows'] = {
            'load': pipeline_window(samples, begin, begin+spec['duration_seconds']),
            'last_half_load': pipeline_window(samples, begin+spec['duration_seconds']/2, begin+spec['duration_seconds']),
            'recovery': pipeline_window(samples, begin+spec['duration_seconds'], begin+spec['duration_seconds']+spec['recovery_seconds'])}
        verification = json.loads((folder/'verification.json').read_text())
        verified = verification['verified']
        report['failed_jobs_before_teardown'] = verification['failed_jobs']
        report['after_timing_search_publications_verified'] = sum(row.get('semantic_probe_pass') is True for row in verified)
        report['after_timing_db_processed_status_count'] = sum(row['status'] == 'processed' for row in verified)
        report['pipeline_samples'] = [{'seconds_from_load_start': row['epoch']-begin, **row['pipeline']} for row in samples
                                      if begin <= row['epoch'] <= begin+spec['duration_seconds']+spec['recovery_seconds']]
        trials.append(report)
    assert sum(row['uploads']['planned'] for row in trials) == plan['planned_upload_arrivals']
    assert sum(row['query_ledger']['planned'] for row in trials) == plan['planned_query_arrivals']
    return {'registered_plan_sha256': hashlib.sha256(registered.read_bytes()).hexdigest(),
            'started_at': progress['started_at'], 'finished_at': progress['finished_at'], 'trials': trials,
            'limits': ['One paired repetition per rate; exploratory confirmation only.',
                       'Worker containers restart for each trial, including lazy model startup.',
                       'Completion rates come from sampled database status; playback is not decoded by this measurement.',
                       'Verification after timing may observe more completed work than the final timed sample.',
                       'Admission success does not establish sustainable processing capacity.']}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('root', type=Path)
    parser.add_argument('--registered', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report = summarize(args.root, args.registered)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'trials': len(report['trials']), 'output': str(args.output)}))


if __name__ == '__main__': main()
