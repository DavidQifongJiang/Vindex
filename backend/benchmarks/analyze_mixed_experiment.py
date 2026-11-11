"""Offline analysis; scheduled arrivals and failures always remain in denominators."""
import argparse
import json
import math
from pathlib import Path


def quantile(values, q):
    return sorted(values)[max(0, math.ceil(len(values)*q)-1)] if values else None


def ledger(rows, planned, success):
    indices = [row['index'] for row in rows]
    complete = len(rows) == planned and set(indices) == set(range(planned))
    successes = sum(row['outcome'] == success for row in rows)
    return {'planned': planned, 'recorded': len(rows), 'successes': successes,
            'success_fraction': successes/planned if planned else None,
            'complete_unique_ledger': complete}


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def analyze(folder):
    spec = json.loads((folder/'spec.json').read_text())
    client = folder/'client'
    queries, uploads = read_rows(client/'queries.jsonl'), read_rows(client/'uploads.jsonl')
    phases = {}
    for phase, duration in [('baseline', spec['baseline_seconds']), ('load', spec['duration_seconds']), ('recovery', spec['recovery_seconds'])]:
        rows = [r for r in queries if r['phase'] == phase]
        phases[phase] = {**ledger([{**r, 'index': i} for i, r in enumerate(rows)], math.ceil(spec['search_rps']*duration-1e-9), 'success'),
            'p50_seconds': quantile([r['seconds'] for r in rows], .5), 'p95_seconds': quantile([r['seconds'] for r in rows], .95),
            'max_seconds': max([r['seconds'] for r in rows] or [0]), 'lag_p99_seconds': quantile([r['lag_seconds'] for r in rows], .99)}
    query_ledger = ledger(queries, math.ceil(spec['search_rps']*(spec['baseline_seconds']+spec['duration_seconds']+spec['recovery_seconds'])-1e-9), 'success')
    upload_ledger = ledger(uploads, math.ceil(spec['uploads_per_minute']/60*spec['duration_seconds']-1e-9), 'accepted')
    accepted = [r for r in uploads if r['outcome'] == 'accepted']
    baseline, load = phases['baseline'], phases['load']
    ratio = load['p95_seconds']/baseline['p95_seconds'] if load['p95_seconds'] and baseline['p95_seconds'] else None
    no_slots = sum(r.get('failure_stage') == 'pool_full' for r in uploads)
    client_complete = all(json.loads((client/name).read_text()).get('finished') is True for name in ['client-result.json', 'query-completion.json', 'upload-completion.json'])
    valid = client_complete and query_ledger['complete_unique_ledger'] and upload_ledger['complete_unique_ledger'] and no_slots == 0
    valid = valid and all(p['lag_p99_seconds'] is not None and p['lag_p99_seconds'] <= .1 for p in phases.values())
    valid = valid and (not uploads or quantile([r['lag_seconds'] for r in uploads], .99) <= .1)
    report = {'name': spec['name'], 'mode': spec['mode'], 'processing': spec['processing'], 'file': Path(spec['file']).name,
              'rate': spec['uploads_per_minute'], 'search_rps': spec['search_rps'], 'phases': phases, 'query_ledger': query_ledger,
              'uploads': {**upload_ledger, 'pool_full': no_slots, 'accepted_p95_seconds': quantile([r['upload_seconds'] for r in accepted], .95)},
              'search_p95_baseline_ratio': ratio, 'arrival_validation_pass': valid,
              'search_slo_pass': bool(valid and load['success_fraction'] >= .99 and load['p95_seconds'] <= 1 and ratio <= 1.2),
              'upload_success_pass': bool(valid and (upload_ledger['success_fraction'] is None or upload_ledger['success_fraction'] >= .99))}
    go = json.loads((client/'go.json').read_text())['start_epoch']
    begin, end = go+spec['baseline_seconds'], go+spec['baseline_seconds']+spec['duration_seconds']
    report['uploads']['completed_during_load_per_minute'] = sum(begin <= r['completed_epoch'] < end for r in accepted)*60/spec['duration_seconds']
    report['uploads']['outstanding_at_load_end'] = sum(r.get('started_epoch', math.inf) < end and r.get('completed_epoch', math.inf) >= end for r in uploads if r['outcome'] != 'not_started')
    samples = [r for r in read_rows(folder/'server-samples.jsonl') if begin <= r['epoch'] <= end]
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        elapsed = last['epoch']-first['epoch']
        cpu = lambda row: int(row['containers']['bench-api']['cpu']['usage_usec'])/1e6
        report['api_cpu_fraction_of_one_core'] = (cpu(last)-cpu(first))/elapsed
        report['pipeline_start'] = first['pipeline']; report['pipeline_end'] = last['pipeline']
        report['pipeline_pending_growth'] = last['pipeline']['pending_videos']-first['pipeline']['pending_videos']
        report['searchable_completions_per_minute'] = (last['pipeline']['searchable']-first['pipeline']['searchable'])*60/elapsed
        report['playable_completions_per_minute'] = (last['pipeline']['playable']-first['pipeline']['playable'])*60/elapsed
    return report


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('root', type=Path); parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.root.rglob('spec.json')):
        if path.parent.name == 'client' or not (path.parent/'client/client-result.json').exists(): continue
        rows.append(analyze(path.parent))
    value = {'trials': rows}
    if args.output: args.output.write_text(json.dumps(value, indent=2)+'\n')
    for r in rows:
        print(json.dumps({k: r[k] for k in ['name', 'rate', 'mode', 'arrival_validation_pass', 'search_slo_pass', 'search_p95_baseline_ratio', 'uploads']} | {'search_p95': r['phases']['load']['p95_seconds']}))


if __name__ == '__main__': main()
