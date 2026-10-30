"""Primary retrieval metrics with explicit missing judgments and provenance."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent/'library_queries_500'
METRICS = ['hit_at_1', 'hit_at_5', 'mrr_at_5']


def primary_bounds(grades):
    """Missing labels bound a score; missing result slots do not contain answers."""
    if len(grades) > 5 or any(g is not None and (type(g) is not int or g not in (0,1,2)) for g in grades):
        raise ValueError('Expected at most five grades: 0, 1, 2, or None')
    def score(values):
        first = next((i for i,g in enumerate(values,1) if g == 2), None)
        return dict(hit_at_1=int(bool(values) and values[0]==2),hit_at_5=int(first is not None),mrr_at_5=1/first if first else 0)
    lower = score([0 if g is None else g for g in grades])
    upper = score([2 if g is None else g for g in grades])
    return {m:dict(lower=lower[m],upper=upper[m],value=lower[m] if lower[m]==upper[m] else None) for m in METRICS}


def aggregate(rows):
    out = {'queries':len(rows)}
    for m in METRICS:
        lower = mean(r['bounds'][m]['lower'] for r in rows) if rows else None
        upper = mean(r['bounds'][m]['upper'] for r in rows) if rows else None
        determined = sum(r['bounds'][m]['value'] is not None for r in rows)
        out[m] = dict(value=lower if rows and determined==len(rows) else None,
                      lower_bound=lower,upper_bound=upper,determined_queries=determined)
    return out


def abstention_metrics(rows, answerability):
    """answerability contains verified 'answerable'/'unanswerable' labels only."""
    if any(v not in ('answerable','unanswerable') for v in answerability.values()):
        raise ValueError('Answerability must be explicitly verified')
    successful = [r for r in rows if not r.get('error')]
    out = {}
    for kind, name in [('unanswerable','correct_abstention_rate'),('answerable','incorrect_abstention_rate')]:
        judged = [r for r in successful if answerability.get(r['query_id'])==kind]
        out[name] = dict(verified_queries=len(judged),abstentions=sum(not r['results'] for r in judged),
                         value=mean(not r['results'] for r in judged) if judged else None)
    out['request_errors'] = sum(bool(r.get('error')) for r in rows)
    return out


def main():
    protocol_path = ROOT/'metrics_protocol_v1.json'
    raw_path = ROOT/'full500_results.json'
    qrels_path = ROOT/'comparison_pooled_judgments.json'
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    qrels = json.loads(qrels_path.read_text(encoding='utf-8'))
    labels = {(r['query_id'],r['passage_id']):r for r in qrels}
    if len(labels) != len(qrels):
        raise ValueError('Duplicate relevance judgments')
    first = [r for r in raw['rows'] if r['round']==0]
    if len({(r['query_id'],r['mode']) for r in first}) != len(first):
        raise ValueError('Duplicate query/method measurement')
    modes = raw['configured_modes']
    views = {}
    for view in ['human_verified_only','human_plus_ai_draft_provisional']:
        scored = []
        for r in first:
            if r['query_type']=='no_answer_candidate':
                continue
            grades = []
            for h in r['results']:
                known = labels.get((r['query_id'],h['passage_id']))
                accepted = known and (view!='human_verified_only' or known['provenance'].startswith('human_'))
                grades.append(known['rating'] if accepted else None)
            if r.get('error'):
                bounds = {m:dict(value=None,lower=0,upper=1) for m in METRICS}
            else:
                bounds = primary_bounds(grades)
            scored.append(dict(query_id=r['query_id'],mode=r['mode'],split=r['split'],query_type=r['query_type'],
                grades=grades,bounds=bounds,fully_judged=not r.get('error') and all(g is not None for g in grades)))
        common = set.intersection(*[{r['query_id'] for r in scored if r['mode']==m and r['fully_judged']} for m in modes])
        views[view] = dict(
            all_answer_seeking={m:aggregate([r for r in scored if r['mode']==m]) for m in modes},
            by_split={s:{m:aggregate([r for r in scored if r['split']==s and r['mode']==m]) for m in modes}
                      for s in ['development','reserved_evaluation']},
            comparable_fully_judged_subset=dict(query_ids=sorted(common),query_count=len(common),
                methods={m:aggregate([r for r in scored if r['mode']==m and r['query_id'] in common]) for m in modes}),
            rows=scored)
    # No query-level corpus-wide answerability adjudication has yet been provided.
    # A grade2 passage is positive evidence but this script does not auto-promote
    # candidate query labels or AI drafts into a human-verified corpus label.
    no_answer = {m:abstention_metrics([r for r in first if r['mode']==m],{}) for m in modes}
    summary = dict(protocol_version='1.0',views=views,no_answer_metrics=no_answer,
        latency={m:{k:raw['summary'][m][k] for k in ['p50_ms','p95_ms','errors','requests']} for m in modes},
        qrel_provenance=dict(Counter(r['provenance'] for r in qrels)),
        hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in [protocol_path,raw_path,qrels_path]},
        limits=['Bounds describe missing-label uncertainty, not statistical confidence.',
                'The fully judged subset is selected and provisional, not representative of all500.',
                '450 answer-seeking queries and50 negative candidates are intent categories, not exhaustive answerability labels.',
                'No query-level answerability labels means abstention rates remain unavailable.',
                'Latency uses three rounds; quality counts each query once.',
                'Existing labels apply to these exact caption passages, not arbitrary replacement application transcripts.'])
    (ROOT/'primary_metrics_scorecard.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    common = views['human_plus_ai_draft_provisional']['comparable_fully_judged_subset']
    lines = ['# Approved primary search scorecard', '',
        '**Primary metrics: Hit@1, Hit@5, MRR@5.** Only grade2 answers count. '
        'Partial answers remain available for secondary graded nDCG. Source-lecture matches are excluded from these accuracy metrics.', '',
        '## Currently scoreable comparison', '',
        f"The common fully judged subset contains **{common['query_count']} selected development queries**, using a pool with30 human-verified and311 AI-draft judgments. "
        'These are provisional mixed-provenance scores, not all500 accuracy. No complete common subset exists using human labels alone.', '',
        '| Method | Hit@1 | Hit@5 | MRR@5 | p95 backend latency |',
        '| --- | ---: | ---: | ---: | ---: |']
    for m,values in common['methods'].items():
        lines.append(f"| {m} | {values['hit_at_1']['value']:.1%} | {values['hit_at_5']['value']:.1%} | "
                     f"{values['mrr_at_5']['value']:.3f} | {summary['latency'][m]['p95_ms']:.1f} ms |")
    lines += ['', 'Latency covers all500 queries, three rounds, on the local backends; it is a separate denominator and excludes application API/ingress.', '',
        '## Full-set status', '',
        '- All450 answer-seeking queries: primary point estimates remain unavailable where judgments are incomplete.',
        '- Development135 and reserved315 answer-seeking queries are scored separately in the JSON, with explicit missing-label bounds.',
        '- Human-only and human-plus-AI views are separate. Unknown is not treated as irrelevant.',
        '- A known answer can determine Hit@5 even when other passages are unjudged. MRR remains uncertain if an earlier passage is unjudged.',
        '- No-answer rates remain unavailable without verified query-level answerability labels. Returning an irrelevant passage is not proof that the entire library has no answer.', '',
        '## Protocol and decision', '',
        'The user approved this metric policy after retrieval execution. It is not retroactively preregistered. '
        'Score the current frozen reserved results, but do not tune against them and claim they remain blind. '
        'Require acceptable Hit@5, compare Hit@1 and MRR@5 with paired query-level uncertainty, then weigh latency and operational simplicity. '
        'No numeric promotion threshold or overall winner has been established.', '',
        'Reproduce with `.venv/Scripts/python.exe -m benchmarks.score_search_metrics`. '
        'See `metrics_protocol_v1.json` for definitions and `primary_metrics_scorecard.json` for per-query bounds and source hashes.', '']
    (ROOT/'primary_metrics_scorecard.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'common_fully_judged_queries':common['query_count'],'methods':common['methods']},indent=2))


if __name__ == '__main__':
    main()
