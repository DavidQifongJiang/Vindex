"""Compare paired API timings and preserved labels; never rejudge text."""
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np

from benchmarks.score_accepted_candidates import accepted_labels, evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    data = json.loads((args.run / 'before_after_api500_results.json').read_text())
    rows = data['rows']
    expected = 500 * len(data['plan']['modes']) * data['plan']['rounds'] * 2
    if len(rows) != expected or 'cleanup' not in data:
        raise ValueError('Comparison not complete')
    grouped = {(r['query_id'], r['mode'], r['round'], r['variant']): r for r in rows}
    if len(grouped) != expected:
        raise ValueError('Duplicate comparison rows')
    differences = []
    def identity(row):
        return [(h['video_id'], h['start_seconds'], h['end_seconds'], h['text'], h.get('score'))
                for h in row['results']]
    for (query, mode, repeat, variant), row in grouped.items():
        if variant == 'before' and identity(row) != identity(grouped[query, mode, repeat, 'after']):
            differences.append(dict(query_id=query, mode=mode, round=repeat))
    latency = {}
    for mode in data['plan']['modes']:
        latency[mode] = {}
        for variant in ['before', 'after']:
            subset = [r for r in rows if r['mode'] == mode and r['variant'] == variant]
            latency[mode][variant] = dict(requests=len(subset),
                p50_ms=float(np.percentile([r['latency_ms'] for r in subset], 50)),
                p95_ms=float(np.percentile([r['latency_ms'] for r in subset], 95)),
                statuses=dict(Counter(str(r['http_status']) for r in subset)),
                degraded=sum(r['degraded'] for r in subset))
    rankings = [dict(r, method=r['mode'] + '_' + r['variant'],
        results=[dict(h, start=h['start_seconds'], end=h['end_seconds']) for h in r['results']])
        for r in rows if r['round'] == 0]
    quality, scored, pending = evaluate(rankings, accepted_labels())
    result = dict(requests=len(rows), queries=500, latency=latency,
        changed_rankings=differences, quality=quality, cleanup=data['cleanup'],
        limits=['Local ASGI API benchmark; excludes ingress, TLS and JWT verification.',
                'Two sequential rounds, randomized paired variant/mode order; not a concurrency load test.',
                'Existing user-accepted labels retained, including their known limitations; unknown labels stay unknown.',
                'Quality uses first round only; all rounds checked for output equality.'])
    (args.run / 'comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (args.run / 'quality_scores.json').write_text(json.dumps(scored, indent=2), encoding='utf-8')
    lines = ['# Qdrant client reuse comparison', '',
             f'{len(rows)} API requests across 500 queries. Changed paired result lists: {len(differences)}.', '',
             '| Mode | Before p50 / p95 (ms) | After p50 / p95 (ms) |',
             '|---|---:|---:|']
    for mode, values in latency.items():
        b, a = values['before'], values['after']
        lines.append(f"| {mode} | {b['p50_ms']:.1f} / {b['p95_ms']:.1f} | {a['p50_ms']:.1f} / {a['p95_ms']:.1f} |")
    lines += ['', f'Unjudged result pairs: {len(pending)}. No new AI grading was performed.',
              'See comparison.json for HTTP/degradation counts and Hit@1, Hit@5 and MRR bounds.', '', *result['limits']]
    (args.run / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(latency=latency, changed_rankings=len(differences), unjudged=len(pending))))


if __name__ == '__main__':
    main()
