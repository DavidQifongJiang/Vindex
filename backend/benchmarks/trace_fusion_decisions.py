"""Offline fusion audit; ranks and unknown-label bounds, never auto promotion."""
import json
from hashlib import sha256
from pathlib import Path

from app.services.hybrid_search import reciprocal_rank_fusion
from benchmarks.candidate_diagnostics import content_key, analyze
from benchmarks.score_accepted_candidates import accepted_labels, evaluate


def main():
    source = Path('tmp/search-quality-v2/run-01/results.json')
    raw = json.loads(source.read_text())
    corpus = json.loads(source.with_name('corpus.json').read_text())
    labels = accepted_labels()
    validation, _ = analyze(raw, labels, corpus)
    if validation['request_failures']:
        raise ValueError('Incomplete capture')
    output = Path('tmp/search-quality-v2/fusion-audit-01')
    output.mkdir(exist_ok=False)
    plan = dict(source_sha256=sha256(source.read_bytes()).hexdigest(), k_values=[60, 10, 0],
                weights='equal', candidate_depth=50, selection='development only; no automatic winner',
                rationale='Test whether reducing RRF smoothing removes unconditional dual-list dominance.')
    (output / 'plan.json').write_text(json.dumps(plan, indent=2))
    traces, rankings = [], []
    for row in raw['rows']:
        if row['round'] or row['profile'] != 'baseline':
            continue
        branches = row['candidates']['rankings']
        branch_ranks = {b:{h['passage_id']:i+1 for i,h in enumerate(hits)} for b,hits in branches.items()}
        variants = {k:reciprocal_rank_fusion(branches['semantic'], branches['lexical'], limit=100, k=k) for k in plan['k_values']}
        ranks = {k:{h['passage_id']:i+1 for i,h in enumerate(hits)} for k,hits in variants.items()}
        for k,hits in variants.items():
            rankings.append(dict(query_id=row['query_id'], query=row['query'], query_type=row['query_type'], method='rrf_k'+str(k), results=hits[:5]))
        baseline_grades = [labels.get(content_key(row['query'],h),{}).get('rating') for h in variants[60][:5]]
        candidates = []
        for h in variants[60]:
            grade = labels.get(content_key(row['query'],h),{}).get('rating')
            pid = h['passage_id']
            if grade != 2 and not any(ranks[k][pid] <= 5 for k in variants):
                continue
            contributions = {str(k):{b:(1/(k+rs[pid]) if pid in rs else 0) for b,rs in branch_ranks.items()} for k in variants}
            candidates.append(dict(h, accepted_grade=grade, semantic_rank=branch_ranks['semantic'].get(pid),
                lexical_rank=branch_ranks['lexical'].get(pid), fusion_ranks={str(k):ranks[k][pid] for k in variants},
                contributions=contributions, cutoff_margins={str(k):sum(contributions[str(k)].values())-variants[k][min(4,len(variants[k])-1)]['score'] for k in variants}))
        traces.append(dict(query_id=row['query_id'], query=row['query'], query_type=row['query_type'],
                           baseline_grades=baseline_grades, baseline_miss=2 not in baseline_grades,
                           candidates=candidates))
    summary, scores, pending = evaluate(rankings, labels)
    for name,data in [('traces',traces),('summary',summary),('scores',scores),('pending',pending)]:
        (output/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
