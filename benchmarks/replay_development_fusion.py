"""Controlled fusion replay over the already judged top-five candidate union."""
from collections import defaultdict
import json
from pathlib import Path
from time import perf_counter
import statistics
from benchmarks.evaluate_development_comparison import metrics, average, bootstrap

ROOT = Path(__file__).resolve().parent/'library_queries_500'


def fuse(dense, sparse, weight, k=60):
    scores, documents = defaultdict(float), {}
    for ranking, branch_weight in [(dense, weight), (sparse, 1-weight)]:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = hit['passage_id']
            if key in seen:
                continue
            seen.add(key)
            scores[key] += branch_weight/(k+rank)
            documents[key] = hit
    return [documents[key] for key in sorted(scores, key=lambda key:(-scores[key],key))[:5]]


def main():
    plan = json.loads((ROOT/'comparison_fusion_plan.json').read_text())
    base = json.loads((ROOT/'comparison_results.json').read_text())
    original = json.loads((ROOT/'development_smoke_results.json').read_text())
    pool = json.loads((ROOT/'comparison_pooled_judgments.json').read_text())
    qrels = {(r['query_id'],r['passage_id']):r['rating'] for r in pool}
    ids = sorted({r['query_id'] for r in pool})
    lookup = {(r['query_id'],r['mode']):r for r in original['rows']}
    results, summaries, timings, comparisons = [], {}, {}, {}
    for weight in plan['semantic_weights']:
        name = f'rrf_depth5_semantic_weight_{weight}'
        times, negatives = [], []
        for query_id in ids:
            dense, sparse = lookup[(query_id,'semantic')], lookup[(query_id,'lexical')]
            start = perf_counter()
            ranked = fuse(dense['results'], sparse['results'], weight)
            times.append((perf_counter()-start)*1000)
            grades = [qrels[(query_id,h['passage_id'])] for h in ranked]
            ideal = [r['rating'] for r in pool if r['query_id']==query_id]
            row = dict(query_id=query_id, mode=name, query_type=dense['query_type'], grades=grades,
                       passage_ids=[h['passage_id'] for h in ranked], **metrics(grades,ideal))
            results.append(row)
            if row['query_type']=='no_answer_candidate':
                negatives.append(row)
        positive = [r for r in results if r['mode']==name and r['query_type']!='no_answer_candidate']
        summaries[name] = average(positive)
        summaries[name]['negative_candidates_returning_only_grade0'] = sum(not any(r['grades']) for r in negatives)
        timings[name] = dict(mean_fusion_only_ms=statistics.mean(times), note='In-memory ranking only, NOT full search latency')
        ref = {r['query_id']:r for r in base['quality_rows'] if r['mode']=='semantic'}
        comparisons[name+' minus semantic'] = bootstrap([r['answer_hit5']-ref[r['query_id']]['answer_hit5'] for r in positive])
    out = dict(plan=plan, summaries=summaries, fusion_only_timings=timings, paired_hit5_differences=comparisons,
               rows=results, provenance=base['provenance'], production_changes=False, reserved_queries_executed=0)
    (ROOT/'comparison_fusion_results.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(summaries,indent=2))


if __name__ == '__main__':
    main()
