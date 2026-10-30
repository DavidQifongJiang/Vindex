"""Evaluate a completely assessed retrieved pool without treating unjudged as zero."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parent / 'library_queries_500'
MODES = ['semantic', 'lexical', 'hybrid']


def metrics(grades, ideal_grades):
    if any(g not in (0, 1, 2) for g in grades + ideal_grades):
        raise ValueError('Missing or invalid relevance judgment')
    first = next((i for i, g in enumerate(grades[:5], 1) if g == 2), None)
    dcg = sum((2**g-1)/math.log2(i+2) for i, g in enumerate(grades[:5]))
    ideal = sum((2**g-1)/math.log2(i+2) for i, g in enumerate(sorted(ideal_grades, reverse=True)[:5]))
    return {'answer_hit1': int(bool(grades) and grades[0] == 2), 'answer_hit5': int(first is not None),
            'answer_mrr5': 1/first if first else 0, 'answer_precision5': sum(g == 2 for g in grades[:5])/5,
            'partial_or_answer_hit5': int(any(g >= 1 for g in grades[:5])),
            'pooled_ndcg5': dcg/ideal if ideal else 0, 'pool_has_any_relevance': bool(ideal)}


def average(rows):
    keys = ['answer_hit1', 'answer_hit5', 'answer_mrr5', 'answer_precision5', 'partial_or_answer_hit5', 'pooled_ndcg5']
    return {'queries': len(rows), **{k: statistics.mean(r[k] for r in rows) if rows else None for k in keys}}


def bootstrap(differences):
    rng = random.Random(20260917)
    samples = sorted(statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(10000))
    return {'mean_difference': statistics.mean(differences), 'paired_bootstrap_95_interval': [samples[249], samples[9749]],
            'note': 'Sampling variability within this selected query set only; not judgment uncertainty or population representativeness.'}


def main():
    plan = json.loads((ROOT/'comparison_experiment_plan.json').read_text())
    raw_path = ROOT/'development_smoke_results.json'
    if sha256(raw_path.read_bytes()).hexdigest() != plan['quality_input_sha256']:
        raise ValueError('Frozen quality input changed')
    original = json.loads(raw_path.read_text())
    pool = json.loads((ROOT/'comparison_pooled_judgments.json').read_text())
    supplied = json.loads((ROOT/'comparison_ai_grades.json').read_text())
    session = json.loads((ROOT/'human_review_session.json').read_text())
    human = {(i['query_id'], i['candidate']['passage']['passage_id']): i for i in session['items']}
    grouped = {}
    for r in pool:
        grouped.setdefault(r['sample_id'], []).append(r)
    if set(grouped) != set(supplied):
        raise ValueError('Missing query assessments')
    for sample, entries in grouped.items():
        entries.sort(key=lambda r: r['assessment_id'])
        grades = supplied[sample]
        if len(grades) != len(entries):
            raise ValueError('Assessment count mismatch')
        for r, grade in zip(entries, grades):
            confirmed = human.get((r['query_id'],r['passage_id']))
            if grade not in [0,1,2]:
                raise ValueError('Invalid grade')
            if confirmed:
                if grade != confirmed['human_rating']:
                    raise ValueError('Attempt to change human-confirmed rating')
                r.update(rating=grade, provenance=confirmed['human_verification']['provenance'],
                         rationale=confirmed.get('ai_assessment', {}).get('reason', confirmed['human_notes']))
            else:
                r.update(rating=grade, provenance='ai_draft', rationale={
                    0: 'Displayed passage does not substantively answer the requested mechanism, condition, or example.',
                    1: 'Contains substantive information addressing part of the query, but omits a required mechanism, condition, or example.',
                    2: 'Displayed passage supplies the requested explanation or procedure.'}[grade])
    qrels = {(r['query_id'],r['passage_id']):r for r in pool}
    if len(qrels) != len(pool):
        raise ValueError('Duplicate judgment')
    (ROOT/'comparison_pooled_judgments.json').write_text(json.dumps(pool, indent=2), encoding='utf-8')
    rows, negatives = [], []
    for r in original['rows']:
        if r['query_id'] not in plan['query_ids']:
            continue
        if r.get('error'):
            raise ValueError('Quality input has request errors')
        grades = [qrels[(r['query_id'],h['passage_id'])]['rating'] for h in r['results']]
        ideal = [x['rating'] for x in pool if x['query_id'] == r['query_id']]
        scored = dict(query_id=r['query_id'], mode=r['mode'], query_type=r['query_type'], grades=grades,
                      **metrics(grades, ideal))
        (negatives if r['query_type'] == 'no_answer_candidate' else rows).append(scored)
    by_mode = {m: average([r for r in rows if r['mode']==m]) for m in MODES}
    paired = {}
    for a, b in [('hybrid','semantic'),('hybrid','lexical'),('semantic','lexical')]:
        aa = {r['query_id']:r for r in rows if r['mode']==a}
        bb = {r['query_id']:r for r in rows if r['mode']==b}
        paired[a+' minus '+b] = {k: bootstrap([aa[q][k]-bb[q][k] for q in sorted(aa)]) for k in ['answer_hit5','pooled_ndcg5']}
    repeated = json.loads((ROOT/'comparison_latency_results.json').read_text())
    expected = {(r['query_id'],r['mode']): [h['passage_id'] for h in r['results']] for r in original['rows']}
    stable = sum(expected[(r['query_id'],r['mode'])] == [h['passage_id'] for h in r['results']] for r in repeated['rows'])
    latency = {m: {k: repeated['summary'][m][k] for k in ['requests','errors','p50_ms','p95_ms']} for m in MODES}
    contexts = json.loads((ROOT/'comparison_context_experiment.json').read_text())
    if any(c['after_rating'] not in [0,1,2] for c in contexts):
        raise ValueError('Context experiment unassessed')
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), quality_queries=30, answer_seeking_queries=25,
        negative_candidates=5, unique_assessed_pairs=len(pool), provenance=dict(Counter(r['provenance'] for r in pool)),
        grade_counts=dict(Counter(str(r['rating']) for r in pool)), judgment_coverage=1.0,
        quality=by_mode, by_type={t:{m:average([r for r in rows if r['mode']==m and r['query_type']==t]) for m in MODES}
                                 for t in sorted({r['query_type'] for r in rows})},
        paired_comparisons=paired, quality_rows=rows, negative_rows=negatives,
        negative_candidate_behavior={m:{'queries':len([r for r in negatives if r['mode']==m]),
            'queries_returning_only_grade0_passages':sum(bool(r['grades']) and not any(r['grades']) for r in negatives if r['mode']==m)} for m in MODES},
        latency=latency, latency_query_count=150, latency_rounds=repeated['repetitions'],
        exact_rank_list_stability={'identical_to_original':stable,'compared':len(repeated['rows'])},
        context=dict(cases=len(contexts), strict_answers_before=sum(c['before_rating']==2 for c in contexts),
            strict_answers_after=sum(c['after_rating']==2 for c in contexts),
            mean_words_before=statistics.mean(c['before_words'] for c in contexts),
            mean_words_after=statistics.mean(c['after_words'] for c in contexts),
            partial_or_better_before=sum(c['before_rating']>=1 for c in contexts),
            partial_or_better_after=sum(c['after_rating']>=1 for c in contexts)),
        setup=dict(seconds=repeated['index_setup_seconds'], model_load_seconds=repeated['model_load_seconds'],
            elasticsearch_store_bytes=repeated['elasticsearch_store_bytes'],
            nominal_float32_vector_bytes=repeated['passages']*384*4,
            note='Nominal raw vectors are not Qdrant disk/memory usage; not a cost equivalence.'),
        cleanup=repeated['cleanup'], limitations=plan['limits']+[
            'Quality denominators are 25 candidate-answerable queries, not 135 or 150.',
            'Most new relevance labels are AI drafts. Human agreement on earlier 30 does not extend to these.',
            'nDCG is zero when the retrieved union contains no judged relevant passage; pooled ideals are incomplete.',
            'Context case 1 retains its original human grade but new text uses the stricter AI rubric; do not attribute that rating change to treatment harm.',
            'Caption chunks and direct backend timings are not accepted application transcripts or end-to-end API latency.',
            'No new failure/load/auth/ingestion test; earlier phase5 evidence is separate.',
        ])
    report['artifact_hashes']={name:sha256((ROOT/name).read_bytes()).hexdigest() for name in [
        'comparison_experiment_plan.json','comparison_pooled_judgments.json','comparison_context_experiment.json','comparison_latency_results.json']}
    (ROOT/'comparison_results.json').write_text(json.dumps(report, indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['quality','by_type','latency','context','exact_rank_list_stability','provenance']},indent=2))


if __name__ == '__main__':
    main()
