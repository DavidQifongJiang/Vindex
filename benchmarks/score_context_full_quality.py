"""Full paired scorecard with missing-label bounds and separate negative candidates."""
import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from benchmarks.context_full_quality import ROOT
from benchmarks.score_search_metrics import aggregate, primary_bounds, METRICS


def calibration(rows,reference):
    if len({r['review_id'] for r in rows})!=len(rows):raise ValueError('Duplicate judgments')
    if {r['review_id'] for r in rows}!=set(reference):raise ValueError('Incomplete calibration')
    tp=sum(r['rating']==2 and reference[r['review_id']]==2 for r in rows)
    predicted=sum(r['rating']==2 for r in rows);positive=sum(g==2 for g in reference.values())
    result=dict(count=len(rows),agreement=sum(r['rating']==reference[r['review_id']] for r in rows)/len(rows),
        precision=tp/predicted if predicted else 0,recall=tp/positive if positive else 0,
        reference_positive=positive,predicted_positive=predicted,unresolved=sum(r['rating'] is None for r in rows))
    result['passed']=result['agreement']>=.8 and result['precision']>=.85 and result['recall']>=.8 and not result['unresolved']
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--judgments',type=Path,required=True);parser.add_argument('--calibration',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(exist_ok=False)
    plan=json.loads((ROOT/'plan.json').read_text());pool=json.loads((ROOT/'pool.json').read_text());mapping=json.loads((ROOT/'mapping.json').read_text())
    inputs=[json.loads(line) for line in args.judgments.read_text().splitlines()]
    labels={r['review_id']:r for r in inputs};canonical={r['review_id']:r for r in pool}
    if len(inputs)!=len(labels) or not set(labels)<=set(canonical):raise ValueError('Duplicate or foreign judgments')
    for key,r in labels.items():
        if any(r[k]!=canonical[key][k] for k in ['query','text']):raise ValueError('Changed passage')
        if r['rating'] is not None and (type(r['rating']) is not int or r['rating'] not in (0,1,2)):raise ValueError('Invalid grade')
        if r['rating'] and (not r['evidence'] or r['evidence'] not in r['text']):raise ValueError('Invalid evidence')
    reference={r['review_id']:r['rating'] for r in json.loads(Path('tmp/search-quality-v2/context-quality-pilot-01/assessments.json').read_text())}
    cal=calibration([json.loads(l) for l in args.calibration.read_text().splitlines()],reference)
    rows=[]
    for r in mapping:
        grades=[labels.get(key,{}).get('rating') for key in r['review_ids']]
        rows.append(dict(r,grades=grades,bounds=primary_bounds(grades),fully_judged=all(g is not None for g in grades)))
    actual=[(r['query_id'],r['condition']) for r in rows]
    if len(actual)!=1000 or len(set(actual))!=1000:raise ValueError('Incomplete full500 pair matrix')
    positive=[r for r in rows if r['query_type']!='no_answer_candidate']
    if len(positive)!=900:raise ValueError('Wrong answer-seeking denominator')
    summary=dict(queries=500,answer_seeking_queries=450,negative_candidates=50,unique_passages=len(pool),
        assessments_received=len(labels),unresolved_pairs=len(pool)-sum(r['rating'] is not None for r in inputs),
        calibration=cal,assessment_status='AI provisional, not human validated' if cal['passed'] else 'FAILED calibration; diagnostic model scores only, not reliable quality estimates',
        methods={c:aggregate([r for r in positive if r['condition']==c]) for c in plan['conditions']},
        by_split={s:{c:aggregate([r for r in positive if r['condition']==c and r['split']==s]) for c in plan['conditions']} for s in ['development','reserved_evaluation']},
        by_type={t:{c:aggregate([r for r in positive if r['condition']==c and r['query_type']==t]) for c in plan['conditions']} for t in ['lexical','paraphrase','conceptual']},
        negative_diagnostics={c:dict(queries=50,returned_results=sum(bool(r['review_ids']) for r in rows if r['condition']==c and r['query_type']=='no_answer_candidate'),
            model_claimed_answer=sum(2 in r['grades'] for r in rows if r['condition']==c and r['query_type']=='no_answer_candidate')) for c in plan['conditions']},
        limits=['Expanded condition assumes context is opened; original rankings unchanged.',
                'Negative candidates are not independently verified unanswerable; do not infer a false-positive rate.',
                'Existing500 exposed queries, not fresh confirmation. All model grades provisional.',
                'Unknown assessments are not counted as zero. Bounds are missing-label bounds, not confidence intervals.'])
    indexed={(r['query_id'],r['condition']):r for r in positive};qids=sorted({r['query_id'] for r in positive})
    gains=[];losses=[];uncertain=[]
    for q in qids:
        before=indexed[q,'original']['bounds']['hit_at_5']['value'];after=indexed[q,'opened_context_120']['bounds']['hit_at_5']['value']
        if before is None or after is None:uncertain.append(q)
        elif after>before:gains.append(q)
        elif before>after:losses.append(q)
    summary.update(gained=gains,lost=losses,unresolved_query_outcomes=uncertain)
    metadata={r['query_id']:r for r in csv.DictReader(Path('benchmarks/library_queries_500/queries.csv').open(encoding='utf-8'))}
    rng=np.random.default_rng(20260919);uncertainty={}
    for metric in METRICS:
        pairs=[[indexed[q,c]['bounds'][metric]['value'] for c in plan['conditions']] for q in qids]
        if any(None in p for p in pairs):uncertainty[metric]=dict(status='Unresolved labels; no complete-set confidence interval');continue
        delta=np.array([b-a for a,b in pairs]);sample=delta[rng.integers(0,450,size=(10000,450))].mean(axis=1)
        groups={}
        for i,q in enumerate(qids):groups.setdefault(metadata[q]['authoring_video_key'],[]).append(i)
        indices=list(groups.values());clusters=[]
        for _ in range(10000):
            ix=[i for g in rng.integers(0,len(indices),size=len(indices)) for i in indices[g]];clusters.append(delta[ix].mean())
        uncertainty[metric]=dict(delta=float(delta.mean()),paired_query_bootstrap_95=np.quantile(sample,[.025,.975]).tolist(),
                                authoring_lecture_cluster_bootstrap_95=np.quantile(clusters,[.025,.975]).tolist(),clusters=len(groups))
    summary['conditional_model_uncertainty']=uncertainty
    for name,data in [('summary',summary),('scores',rows)]:
        (args.output/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['assessments_received','unresolved_pairs','calibration','assessment_status','methods']},indent=2))


if __name__=='__main__':main()
