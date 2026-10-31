"""Score complete, separately reviewed original/opened-context pilot results."""
import csv
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
import numpy as np
from benchmarks.context_quality_pilot import OUTPUT
from benchmarks.score_search_metrics import primary_bounds, aggregate, METRICS


def read_assessments(pool, path):
    notes = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        item, grade, evidence, rationale = line.split('|', 3)
        item, grade = int(item), int(grade)
        if item in notes or grade not in (0,1,2) or not rationale:
            raise ValueError('Duplicate or invalid assessment')
        notes[item] = (grade,evidence,rationale)
    if set(notes) != {r['item'] for r in pool}:
        raise ValueError('Incomplete review')
    labels = {}
    for r in pool:
        grade,evidence,rationale = notes[r['item']]
        if grade and (not evidence or evidence not in r['text']):
            raise ValueError('Positive grade requires a verbatim evidence excerpt')
        labels[r['review_id']] = dict(r,rating=grade,evidence=evidence,rationale=rationale,
            provenance='ai_review_draft',reviewer='root_assistant',
            masking='Method, original rank and prior grades hidden; length can reveal context condition; not independent human confirmation.')
    return labels


def main():
    pool = json.loads((OUTPUT/'review_pool.json').read_text())
    notes = Path('benchmarks/context_pilot_review_01.txt')
    labels = read_assessments(pool,notes)
    mapping = json.loads((OUTPUT/'mapping.json').read_text())
    plan = json.loads((OUTPUT/'plan.json').read_text())
    expected = {(q,c) for q in plan['query_ids'] for c in plan['conditions']}
    actual = [(r['query_id'],r['condition']) for r in mapping]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Incomplete paired query matrix')
    rows=[]
    for r in mapping:
        if len(r['review_ids']) != 5:raise ValueError('Expected top5 per condition')
        grades=[labels[k]['rating'] for k in r['review_ids']]
        rows.append(dict(r,grades=grades,bounds=primary_bounds(grades)))
    conditions=plan['conditions']
    by_query={q:{c:next(r for r in rows if r['query_id']==q and r['condition']==c) for c in conditions} for q in plan['query_ids']}
    query_metadata={r['query_id']:r for r in csv.DictReader(Path('benchmarks/library_queries_500/development_150.csv').open(encoding='utf-8'))}
    deltas=np.array([[by_query[q][conditions[1]]['bounds'][m]['value']-by_query[q][conditions[0]]['bounds'][m]['value'] for m in METRICS] for q in plan['query_ids']])
    rng=np.random.default_rng(20260918)
    paired=np.mean(deltas[rng.integers(0,len(deltas),size=(10000,len(deltas)))],axis=1)
    groups={}
    for i,q in enumerate(plan['query_ids']):groups.setdefault(query_metadata[q]['authoring_video_key'],[]).append(i)
    group_values=list(groups.values())
    clustered=[]
    for _ in range(10000):
        indices=[i for group in rng.integers(0,len(groups),size=len(groups)) for i in group_values[group]]
        clustered.append(deltas[indices].mean(axis=0))
    uncertainty={m:dict(delta=float(deltas[:,i].mean()),paired_query_bootstrap_95=np.quantile(paired[:,i],[.025,.975]).tolist(),
                       authoring_lecture_cluster_bootstrap_95=np.quantile(np.array(clustered)[:,i],[.025,.975]).tolist()) for i,m in enumerate(METRICS)}
    gains=[q for q,p in by_query.items() if p[conditions[0]]['bounds']['hit_at_5']['value']==0 and p[conditions[1]]['bounds']['hit_at_5']['value']==1]
    losses=[q for q,p in by_query.items() if p[conditions[0]]['bounds']['hit_at_5']['value']==1 and p[conditions[1]]['bounds']['hit_at_5']['value']==0]
    summary=dict(queries=len(by_query),reviewed_unique_passages=len(labels),provenance='ai_review_draft',
        source_hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in [notes,OUTPUT/'plan.json',OUTPUT/'review_pool.json',OUTPUT/'mapping.json']},
        methods={c:aggregate([r for r in rows if r['condition']==c]) for c in conditions},
        by_query_type={t:{c:aggregate([r for r in rows if r['condition']==c and r['query_type']==t]) for c in conditions} for t in plan['query_counts']},
        uncertainty=uncertainty,authoring_lecture_clusters=len(groups),gained=gains,lost=losses,
        grade_counts=dict(Counter(r['rating'] for r in labels.values())),
        limits=['Exploratory selected ten-query pilot, not full500 quality or fresh confirmation.',
                'Expanded condition assumes the user opens context. Collapsed results are unchanged.',
                'Model-origin judgments, not human ground truth. Bootstrap conditions on these grades and cannot measure judge error.',
                'Small strata and lecture counts limit uncertainty estimates. No release decision from this pilot.'])
    for name,data in [('assessments',list(labels.values())),('scores',rows),('summary',summary)]:
        (OUTPUT/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
