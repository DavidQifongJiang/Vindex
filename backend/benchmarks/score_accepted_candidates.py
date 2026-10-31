"""Reuse user-accepted baseline labels without relabeling new passages."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from benchmarks.candidate_diagnostics import content_key, human_labels, analyze
from benchmarks.score_search_metrics import primary_bounds, aggregate
from app.services.hybrid_search import reciprocal_rank_fusion

BASE = Path('benchmarks/library_queries_500')


def accepted_labels():
    grades = {}
    for line in (BASE / 'before_after_judgments.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        grade = row['rating']
        if type(grade) is not int or grade not in (0, 1, 2):
            raise ValueError('Invalid accepted grade')
        grades[(row['query_id'], row['query'], row['text'])] = grade
    labels = {}
    original = json.loads((BASE / 'before_after_api500_results.json').read_text(encoding='utf-8'))
    for row in original['rows']:
        for hit in row['results']:
            normalized = dict(hit, start=hit['start_seconds'], end=hit['end_seconds'])
            grade = grades[(row['query_id'], row['query'], hit['text'])]
            labels[content_key(row['query'], normalized)] = dict(rating=grade,
                provenance='automated_baseline_label_accepted_by_user')
    labels.update(human_labels(BASE / 'human_review_session.json'))
    return labels


def evaluate(rankings, labels):
    scored, pending = [], {}
    for row in rankings:
        grades = []
        for hit in row['results']:
            key = content_key(row['query'], hit)
            grade = labels.get(key, {}).get('rating')
            grades.append(grade)
            if grade is None:
                pending[key] = dict(review_id=key, query_id=row['query_id'], query=row['query'],
                    video_id=hit['video_id'], start=hit['start'], end=hit['end'], text=hit['text'], rating=None)
        if row['query_type'] != 'no_answer_candidate':
            scored.append(dict(query_id=row['query_id'], query_type=row['query_type'], method=row['method'],
                grades=grades, fully_labeled=all(g is not None for g in grades), bounds=primary_bounds(grades)))
    methods = sorted({r['method'] for r in scored})
    common = set.intersection(*[{r['query_id'] for r in scored if r['method'] == m and r['fully_labeled']} for m in methods]) if methods else set()
    summary = dict(methods={m:dict(all_query_bounds=aggregate([r for r in scored if r['method'] == m]),
        fully_labeled_queries=sum(r['fully_labeled'] for r in scored if r['method'] == m),
        common_subset=aggregate([r for r in scored if r['method'] == m and r['query_id'] in common])) for m in methods},
        common_fully_labeled_queries=len(common), common_query_ids=sorted(common),
        unjudged_top5_pairs=len(pending),
        interpretation='Existing automated labels accepted by user, with original human labels taking precedence. New passages remain unknown. Common subset is selected by label availability, not representative confirmation.')
    return summary, scored, list(pending.values())


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--rerank', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads((args.run / 'results.json').read_text(encoding='utf-8'))
    corpus = json.loads((args.run / 'corpus.json').read_text(encoding='utf-8'))
    labels = accepted_labels()
    integrity, _ = analyze(raw, labels, corpus)
    if integrity['request_failures']:raise ValueError('Incomplete requests')
    rankings = []
    for row in raw['rows']:
        if row['round'] != 0:continue
        branches = row['candidates']['rankings']
        for mode in ['semantic', 'lexical', 'hybrid']:
            if mode == 'semantic' and row['profile'] != 'baseline':continue
            hits = reciprocal_rank_fusion(branches['semantic'], branches['lexical'], limit=5) if mode == 'hybrid' else branches[mode][:5]
            rankings.append(dict(query_id=row['query_id'], query=row['query'], query_type=row['query_type'],
                method=mode + ':' + row['profile'], results=hits))
    if args.rerank:
        rerank = json.loads((args.rerank / 'results.json').read_text(encoding='utf-8'))
        if rerank['plan']['source_sha256'] != sha256((args.run / 'results.json').read_bytes()).hexdigest():raise ValueError('Rerank source mismatch')
        expected = {(q, d) for q in rerank['plan']['query_ids'] for d in rerank['plan']['depths']}
        actual = [(r['query_id'], r['depth']) for r in rerank['rows']]
        if len(actual) != len(set(actual)) or set(actual) != expected:raise ValueError('Incomplete rerank matrix')
        query_map = {r['query_id']: r for r in raw['plan']['queries']}
        corpus_map = {h['passage_id']: h for h in corpus}
        for row in rerank['rows']:
            if row['query'] != query_map[row['query_id']]['query']:raise ValueError('Rerank query changed')
            for hit in row['results']:
                frozen = corpus_map[hit['passage_id']]
                if any(hit.get(k) != v for k, v in frozen.items()):raise ValueError('Rerank passage changed')
            rankings.append(dict(row, method='rerank:' + str(row['depth'])))
    summary, scores, pending = evaluate(rankings, labels)
    summary['acceptance'] = dict(user_statement='The labels are correct. Now we have the baseline',
        scope='Existing baseline label set only; not individual human review of every automated label or acceptance of new labels.',
        source_sha256=sha256((BASE / 'before_after_judgments.jsonl').read_bytes()).hexdigest())
    summary['capture_sha256'] = sha256((args.run / 'results.json').read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in [('summary', summary), ('scores', scores), ('pending_top5', pending)]:
        (args.output / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')
    lines = ['# Accepted-baseline candidate evaluation', '', summary['interpretation'], '',
        '| Method | Fully labeled queries | Hit@1 bounds | Hit@5 bounds | MRR@5 bounds |',
        '|---|---:|---:|---:|---:|']
    for method, result in summary['methods'].items():
        cells = []
        for metric in ['hit_at_1', 'hit_at_5', 'mrr_at_5']:
            b = result['all_query_bounds'][metric]
            cells.append(f"{b['value']:.4f}" if b['value'] is not None else f"[{b['lower_bound']:.4f}, {b['upper_bound']:.4f}]")
        lines.append(f"| {method} | {result['fully_labeled_queries']} | " + ' | '.join(cells) + ' |')
    lines += ['', 'Bounds reflect unknown judgments, not statistical confidence intervals.', '',
        f"Common fully labeled subset across every listed method: {summary['common_fully_labeled_queries']} queries. Do not compare methods using different fully labeled subsets.", '',
        f"New top-five query/passage pairs requiring assessment: {summary['unjudged_top5_pairs']}. See `pending_top5.json`."]
    if args.rerank:
        lines += ['', '## Offline reranking latency', '', '| Depth | Queries | Median | p95 |', '|---|---:|---:|---:|']
        for depth, result in rerank['summary']['depths'].items():
            lines.append(f"| {depth} | {result['requests']} | {result['p50_rerank_ms']:.1f} ms | {result['p95_rerank_ms']:.1f} ms |")
        lines += ['', 'Rerank-only CPU time on a shared developer machine; not full API or authenticated deployment latency. No serving configuration changed.']
    (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':main()
