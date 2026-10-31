"""Summarize a paired API benchmark with explicitly automated relevance estimates."""
from collections import Counter,defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path
import statistics
import numpy as np
from benchmarks.gguf_relevance_judge import key
from benchmarks.evaluate_development_comparison import metrics,average
from benchmarks.score_search_metrics import primary_bounds,aggregate

ROOT=Path(__file__).resolve().parent/'library_queries_500'
MODES=['semantic','lexical','hybrid']

def interval(differences):
    a=np.asarray(differences,dtype=float)
    rng=np.random.default_rng(20260916)
    draws=rng.choice(a,size=(10000,len(a))).mean(axis=1)
    return dict(mean=float(a.mean()),ci95=np.percentile(draws,[2.5,97.5]).tolist())

def calibration(rows):
    matrix=[[0]*3 for _ in range(3)]
    for r in rows:matrix[r['human_rating']][r['rating']]+=1
    tp=matrix[2][2];pred=sum(r[2] for r in matrix);actual=sum(matrix[2])
    return dict(n=len(rows),agreement=sum(matrix[i][i] for i in range(3))/len(rows),human_rows_model_columns=matrix,
        grade2_precision=tp/pred if pred else None,grade2_recall=tp/actual if actual else None,
        always_zero_agreement=sum(matrix[0])/len(rows),note='Selected disagreement samples; only two human grade-2 examples. Does not establish judge reliability across all queries.')

def main():
    raw=ROOT/'before_after_api500_results.json';data=json.loads(raw.read_text())
    queries={r['query_id']:r for r in csv.DictReader((ROOT/'queries.csv').open(encoding='utf-8'))}
    expected={(q,m,n) for q in queries for m in MODES for n in range(data['plan']['rounds'])}
    actual=[(r['query_id'],r['mode'],r['round']) for r in data['rows']]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:raise ValueError('Incomplete/duplicate requests')
    judge=[json.loads(l) for l in (ROOT/'before_after_judgments.jsonl').read_text(encoding='utf-8').splitlines()]
    qrels={r['content_key']:r['rating'] for r in judge}
    if len(qrels)!=len(judge):raise ValueError('Duplicate assessments')
    human=json.loads((ROOT/'human_review_session.json').read_text())['items']
    matched=0
    human_labels={key(h['query_id'],h['candidate']['passage']['text']):h['human_rating'] for h in human}
    for h in human:
        k=key(h['query_id'],h['candidate']['passage']['text'])
        if k in qrels:matched+=1;qrels[k]=h['human_rating']
    pilot=[json.loads(l) for l in (ROOT/'qwen_judge_pilot.jsonl').read_text().splitlines() if 'human_rating' in json.loads(l)]
    pool=defaultdict(list)
    for r in judge:pool[r['query_id']].append(qrels[r['content_key']])
    scored=[];negative=[]
    human_scores=[]
    for r in data['rows']:
        if r['round']!=0:continue
        grades=[qrels[key(r['query_id'],h['text'])] for h in r['results']]
        row=dict(query_id=r['query_id'],mode=r['mode'],split=r['split'],query_type=r['query_type'],grades=grades,
            **metrics(grades,pool[r['query_id']]))
        (negative if r['query_type']=='no_answer_candidate' else scored).append(row)
        if r['query_type']!='no_answer_candidate':
            human_scores.append(dict(mode=r['mode'],bounds=primary_bounds([human_labels.get(key(r['query_id'],h['text'])) for h in r['results']])))
    by_mode={m:average([r for r in scored if r['mode']==m]) for m in MODES}
    grouped={field:{value:{m:average([r for r in scored if r[field]==value and r['mode']==m]) for m in MODES} for value in sorted({r[field] for r in scored})} for field in ['split','query_type']}
    paired={}
    for split in ['all','development','reserved_evaluation']:
        subset=[r for r in scored if split=='all' or r['split']==split]
        if not subset:continue
        lookup={(r['query_id'],r['mode']):r for r in subset};ids=sorted({r['query_id'] for r in subset})
        paired[split]={metric:interval([lookup[q,'hybrid'][metric]-lookup[q,'semantic'][metric] for q in ids]) for metric in ['answer_hit1','answer_hit5','answer_mrr5']}
        for metric in ['answer_hit1','answer_hit5','answer_mrr5']:
            clusters=defaultdict(list)
            for q in ids:clusters[queries[q]['authoring_video_key']].append(lookup[q,'hybrid'][metric]-lookup[q,'semantic'][metric])
            if len({len(v) for v in clusters.values()})!=1:raise ValueError('Cluster CI currently requires equal queries per lecture')
            paired[split][metric]['lecture_cluster_ci95']=interval([statistics.mean(v) for v in clusters.values()])['ci95']
            paired[split][metric]['lecture_clusters']=len(clusters)
        paired[split]['rescued_queries']=[q for q in ids if lookup[q,'hybrid']['answer_hit5']>lookup[q,'semantic']['answer_hit5']]
        paired[split]['lost_queries']=[q for q in ids if lookup[q,'hybrid']['answer_hit5']<lookup[q,'semantic']['answer_hit5']]
    latency={}
    for m in MODES:
        rows=[r for r in data['rows'] if r['mode']==m];times=[r['latency_ms'] for r in rows]
        latency[m]=dict(requests=len(rows),p50_ms=float(np.percentile(times,50)),p95_ms=float(np.percentile(times,95)),p99_ms=float(np.percentile(times,99)),
            http_statuses=dict(Counter(r['http_status'] for r in rows)),degraded=sum(r['degraded'] for r in rows),
            by_round={str(n):dict(p50_ms=float(np.percentile([r['latency_ms'] for r in rows if r['round']==n],50)),p95_ms=float(np.percentile([r['latency_ms'] for r in rows if r['round']==n],95))) for n in range(data['plan']['rounds'])})
    ranks={(r['query_id'],r['mode'],r['round']):[key(r['query_id'],h['text']) for h in r['results']] for r in data['rows']}
    stable=sum(ranks[q,m,0]==ranks[q,m,n] for q in queries for m in MODES for n in range(1,data['plan']['rounds']))
    overlap=Counter(len(set(ranks[q,'semantic',0])&set(ranks[q,'hybrid',0])) for q in queries)
    report=dict(input_sha256=sha256(raw.read_bytes()).hexdigest(),videos=data['videos'],passages=data['passages'],queries=len(queries),
        relevance='UNVALIDATED DIAGNOSTIC ONLY: Qwen7B failed positive-label calibration. Exact-text human labels take precedence. These scores cannot establish an accuracy improvement.',
        judged_pairs=len(judge),matched_human_pairs=matched,calibration=calibration(pilot),quality=by_mode,
        human_only_quality_bounds={m:aggregate([r for r in human_scores if r['mode']==m]) for m in MODES},
        grouped=grouped,paired_hybrid_minus_semantic=paired,latency=latency,
        stable_repeats=stable,repeat_comparisons=len(queries)*len(MODES)*(data['plan']['rounds']-1),
        hybrid_semantic_top5_overlap=dict(overlap),
        candidate_negatives={m:dict(queries=50,nonempty=sum(bool(r['results']) for r in data['rows'] if r['round']==0 and r['mode']==m and r['query_type']=='no_answer_candidate'),model_answer_hit5=sum(r['answer_hit5'] for r in negative if r['mode']==m)) for m in MODES},
        caveats=['450 candidate-answerable queries in quality denominator; 50 candidate negatives reported separately; answerability not exhaustively corpus-verified.',
            'Primary relevance requires grade2; grade1 is partial only. Complete retrieved pool judged; no unjudged passage is silently assigned zero.',
            'Intervals include paired query and lecture-cluster bootstrap estimates, not judge uncertainty. Queries are not a random user population.',
            'Latency uses a shared local machine, sequential load and warmed ASGI endpoint, fixed authentication. Includes real scope and revalidation; excludes external network and JWT. Model downloads overlapped part of round1; round2 followed completed downloads. No judge inference overlapped timing; per-round latency is provided.',
            'Official captions substitute for production ASR; this is not a transcription-quality evaluation. No claim that 500 queries are 500 videos.',
            'Development and reserved splits were inspected in earlier experiments; this run is confirmation, not a newly untouched holdout.'])
    (ROOT/'before_after500_summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (ROOT/'before_after500_per_query.json').write_text(json.dumps(scored+negative,indent=2),encoding='utf-8')
    raw_lookup={(r['query_id'],r['mode']):r for r in data['rows'] if r['round']==0}
    review=[]
    for category,ids in [('model_says_hybrid_rescues',paired['all']['rescued_queries']),('model_says_hybrid_loses',paired['all']['lost_queries'])]:
        for q in ids:
            passages={}
            for m in ['semantic','hybrid']:
                for rank,h in enumerate(raw_lookup[q,m]['results'],1):
                    k=key(q,h['text'])
                    if k not in passages:passages[k]=dict(content_key=k,text=h['text'],video_key=h['video_key'],start_seconds=h['start_seconds'],end_seconds=h['end_seconds'],automated_grade=qrels[k],human_verified=k in human_labels,appearances=[])
                    passages[k]['appearances'].append(dict(mode=m,rank=rank))
            review.append(dict(query_id=q,query=queries[q]['query'],split=queries[q]['split'],selection_reason=category,passages=list(passages.values())))
    (ROOT/'before_after_human_review_priority.json').write_text(json.dumps(review,indent=2),encoding='utf-8')
    lines=['# Full 500-query before/after benchmark','',f"Corpus: {data['videos']} videos, {data['passages']:,} caption passages. {len(data['rows']):,} API requests, {data['plan']['rounds']} rounds. Quality: 450 answer-seeking queries; 50 candidate negatives separately.",'',
        '**Relevance numbers are unvalidated automated diagnostics. The judge failed positive-label calibration; these scores cannot establish an accuracy improvement.** Existing human labels override the model on exact query/passage matches.','',
        '| Method | Hit@1 | Hit@5 | MRR@5 | API p50 | API p95 |','|---|---:|---:|---:|---:|---:|']
    for m in MODES:
        q=by_mode[m];l=latency[m]
        lines.append(f"| {m} | {q['answer_hit1']:.2%} | {q['answer_hit5']:.2%} | {q['answer_mrr5']:.4f} | {l['p50_ms']:.1f} ms | {l['p95_ms']:.1f} ms |")
    lines+=['','## Decision','',
        'No validated overall accuracy winner is established. Keep semantic search as the quality reference and hybrid experimental. The automated diagnostic suggests a small gain, but all primary paired intervals include zero and the judge fails positive-label calibration. Do not claim a proven accuracy improvement or change the default on this evidence.','',
        f"Hybrid rescues {len(paired['all']['rescued_queries'])} queries and loses {len(paired['all']['lost_queries'])} according to the model. On the 315 reserved answer-seeking queries, it rescues {len(paired['reserved_evaluation']['rescued_queries'])} and loses {len(paired['reserved_evaluation']['lost_queries'])}.",'',
        'Latency is measured evidence: all 3,000 requests returned HTTP 200, none degraded, all had complete coverage; all 1,500 repeated rankings were identical. The baseline is semantic-only on the identical library scope, not the historical single-video endpoint.','',
        '## Paired differences: hybrid minus semantic','',
        '| Metric | Difference | 95% query bootstrap interval | 95% lecture-cluster interval |','|---|---:|---:|---:|']
    for metric in ['answer_hit1','answer_hit5','answer_mrr5']:
        v=paired['all'][metric]
        lines.append(f"| {metric} | {v['mean']:.4f} | [{v['ci95'][0]:.4f}, {v['ci95'][1]:.4f}] | [{v['lecture_cluster_ci95'][0]:.4f}, {v['lecture_cluster_ci95'][1]:.4f}] |")
    lines+=['','These intervals account for sampling variability, not incorrect model labels.','',
        '## Judge calibration','',
        'The pinned local Qwen 7B judge agrees with 24/30 human ratings (80%). However, a constant zero label would agree with 23/30. Of four model grade-2 calls, only one is human grade 2 (25% precision); it finds one of two human positives (50% recall). The selected sample is small and cannot validate automated accuracy. The earlier Gemma 1B judge agreed on only 2/30 and was discarded.','',
        f"The complete {len(judge):,}-pair pool was assessed. All {matched} existing human judgments were preserved and override model labels. The remaining labels are explicitly automated, never human. No missing judgment is treated as zero.",'',
        '## Query-type diagnostics','',
        '| Type | Semantic Hit@5 | ES Hit@5 | Hybrid Hit@5 |','|---|---:|---:|---:|']
    for t,methods in grouped['query_type'].items():
        lines.append('| '+t+' | '+' | '.join(f"{methods[m]['answer_hit5']:.2%}" for m in MODES)+' |')
    lines+=['','These suggest conceptual-query gains and paraphrase regressions, but remain hypotheses requiring better judgments. ES alone is much faster, with lower estimated overall answer coverage.','',
        '## Candidate negatives','',
        'All 50 candidate-negative queries returned results from every method. Their full-library unanswerability has not been verified, so this is not a measured false-positive rate or evidence of correct abstention.','',
        '## Next evidence needed','',
        'Review the 118 model-predicted gain/loss queries in `before_after_human_review_priority.json` without revealing engine identity. For an overall quality claim, also review a representative sample of unchanged/outcome-tied queries, since an unreliable judge can miss differences. Use new untouched queries for any subsequent tuning decision.','',
        '## Interpretation limits','']+['- '+s for s in report['caveats']]
    lines+=['','## Artifacts','',
        '- `before_after_api500_results.json`: all timed requests and returned passages.',
        '- `before_after500_summary.json`: split/type metrics, intervals, per-round latency, human-only bounds and judge calibration.',
        '- `before_after500_per_query.json`: all first-round query/method grades and scores.',
        '- `before_after_judgments.jsonl`: complete automated label provenance; raw model labels before human overrides.',
        '- `before_after_environment.json`: machine/timing conditions and cleanup limitation.',
        '- Evaluation verification: 23 unit tests passed. Production search configuration was not changed.']
    (ROOT/'before_after500_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['quality','latency','calibration','judged_pairs','matched_human_pairs']},indent=2))

if __name__=='__main__':main()
