"""Report all-query diagnostics separately from existing passage-level judgments."""
from collections import Counter
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
from benchmarks.evaluate_development_comparison import metrics, average

ROOT = Path(__file__).resolve().parent/'library_queries_500'


def main():
    raw_path = ROOT/'full500_results.json'
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    plan = json.loads((ROOT/'full500_frozen_plan.json').read_text())
    rows = raw['rows']
    modes = list(plan['methods'])
    assert len(rows) == 500*4*3
    assert len({(r['query_id'],r['mode'],r['round']) for r in rows}) == len(rows)
    assert raw['query_sha256'] == plan['query_sha256']
    assert all(v=='deleted' for v in raw['cleanup'].values())
    first = [r for r in rows if r['round']==0]
    lookup = {(r['query_id'],r['mode']):r for r in first}
    unique_queries = {r['query_id']:r for r in first}
    assert len(unique_queries)==500
    assert Counter(r['split'] for r in unique_queries.values()) == {'development':150,'reserved_evaluation':350}
    paired = []
    for q in sorted(unique_queries):
        s,l,h,t = [lookup[(q,m)] for m in modes]
        paired.append(dict(query_id=q,split=s['split'],query_type=s['query_type'],
            semantic_hit=s.get('authoring_video_in_top5'),lexical_hit=l.get('authoring_video_in_top5'),hybrid_hit=h.get('authoring_video_in_top5'),
            tuned_same_set={x['passage_id'] for x in s['results']}=={x['passage_id'] for x in t['results']},
            tuned_same_order=[x['passage_id'] for x in s['results']]==[x['passage_id'] for x in t['results']]))
    (ROOT/'full500_paired_diagnostics.json').write_text(json.dumps(paired,indent=2),encoding='utf-8')
    known = json.loads((ROOT/'comparison_pooled_judgments.json').read_text())
    labels = {(r['query_id'],r['passage_id']):r for r in known}
    known_queries = {r['query_id'] for r in known}
    coverage = {}
    quality_rows = []
    for mode in modes:
        subset = [r for r in first if r['mode']==mode]
        pairs = [(r['query_id'],h['passage_id']) for r in subset for h in r['results']]
        coverage[mode] = dict(returned_pairs=len(pairs), assessed_pairs=sum(k in labels for k in pairs),
                             human_verified_pairs=sum(k in labels and labels[k]['provenance']!='ai_draft' for k in pairs))
        for r in subset:
            if r['query_id'] not in known_queries or r['query_type']=='no_answer_candidate':
                continue
            keys = [(r['query_id'],h['passage_id']) for h in r['results']]
            if r.get('error') or any(k not in labels for k in keys):
                continue
            grades = [labels[k]['rating'] for k in keys]
            ideal = [x['rating'] for x in known if x['query_id']==r['query_id']]
            quality_rows.append(dict(query_id=r['query_id'],mode=mode,**metrics(grades,ideal)))
    diagnostic = {}
    for split in ['all','development','reserved_evaluation']:
        diagnostic[split] = {}
        for mode in modes:
            sample = [r for r in first if r['mode']==mode and (split=='all' or r['split']==split)]
            positive = [r for r in sample if r['authoring_video_key']]
            negative = [r for r in sample if not r['authoring_video_key']]
            diagnostic[split][mode] = dict(queries=len(sample), candidate_answerable=len(positive),
                authoring_video_hits=sum(r.get('authoring_video_in_top5',False) for r in positive),
                authoring_video_rate=mean(bool(r.get('authoring_video_in_top5')) for r in positive),
                no_answer_candidates=len(negative), candidates_returning_results=sum(bool(r['results']) for r in negative))
    per_type = {t:{m:dict(queries=len(group:=[r for r in first if r['mode']==m and r['query_type']==t]),
                          source_hits=sum(bool(r.get('authoring_video_in_top5')) for r in group))
                  for m in modes} for t in ['lexical','paraphrase','conceptual']}
    ref = {(r['query_id'],r['mode']):[h['passage_id'] for h in r['results']] for r in first}
    stability = sum(ref[(r['query_id'],r['mode'])]==[h['passage_id'] for h in r['results']] for r in rows if r['round']>0)
    pool = {}
    for r in rows:
        for h in r['results']:
            key = (r['query_id'],h['passage_id'])
            old = labels.get(key)
            pool[key] = dict(query_id=r['query_id'], split=r['split'], query_type=r['query_type'],query=r['query'],
                passage_id=h['passage_id'],video_key=h['video_key'],start=h['start'],end=h['end'],text=h['text'],
                relevance=old['rating'] if old else '',provenance=old['provenance'] if old else 'unjudged')
    with (ROOT/'full500_review_pool.csv').open('w',encoding='utf-8',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(next(iter(pool.values()))))
        writer.writeheader();writer.writerows(pool[k] for k in sorted(pool))
    summary = dict(generated_at=datetime.now(timezone.utc).isoformat(),queries=500,requests=len(rows),
        methods=modes,split_diagnostics=diagnostic,by_type_diagnostic=per_type,
        latency={m:{k:raw['summary'][m][k] for k in ['requests','errors','p50_ms','p95_ms']} for m in modes},
        by_split_latency=raw['by_split'],existing_selected_quality={m:average([r for r in quality_rows if r['mode']==m]) for m in modes},
        relevance_coverage=coverage,review_pool_pairs=len(pool),review_pool_provenance=dict(Counter(r['provenance'] for r in pool.values())),
        reserved_answer_accuracy=None,all500_answer_accuracy=None,
        repeat_rank_stability=dict(identical=stability,comparisons=4000),
        tuned_semantic_equivalence=dict(same_top5_set=sum(r['tuned_same_set'] for r in paired),same_order=sum(r['tuned_same_order'] for r in paired),queries=500,
            explanation='At depth5, semantic-only minimum contribution .75/65 exceeds lexical-only maximum .25/61. With five semantic hits, lexical-only passages cannot enter top5.'),
        paired_source_diagnostics={s:dict(
            lexical_rescues_semantic=sum(r['semantic_hit'] is False and r['lexical_hit'] is True for r in paired if s=='all' or r['split']==s),
            semantic_rescues_lexical=sum(r['semantic_hit'] is True and r['lexical_hit'] is False for r in paired if s=='all' or r['split']==s),
            hybrid_rescues_semantic=sum(r['semantic_hit'] is False and r['hybrid_hit'] is True for r in paired if s=='all' or r['split']==s),
            hybrid_loses_semantic=sum(r['semantic_hit'] is True and r['hybrid_hit'] is False for r in paired if s=='all' or r['split']==s)) for s in ['all','reserved_evaluation']},
        raw_sha256=sha256(raw_path.read_bytes()).hexdigest(),frozen_plan=plan,cleanup=raw['cleanup'],
        conclusion='All500 executed. Available metrics establish latency and source-lecture retrieval tradeoffs, not a full500 answer-quality winner. Reserved passage judgments are missing.')
    (ROOT/'full500_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines = ['# All 500 queries: frozen comparison', '',
        f"Executed **500 queries × 4 methods × 3 rounds = {len(rows):,} searches** against all 50 lectures and {raw['passages']:,} caption passages. "
        'Development (150) and reserved evaluation (350) are reported separately. The reserved set is now executed/exposed; no post-run tuning was performed.', '',
        '**Conclusion:** the run measures speed and source-lecture retrieval tradeoffs. It does not establish an overall answer-quality winner: '
        'the reserved queries have no passage relevance judgments. The existing judgments still cover only the selected 30 development queries.', '',
        '## Speed and source-lecture diagnostics', '',
        '**Source-lecture inclusion is not answer accuracy.** It asks whether any top-five passage comes from the lecture used to draft the query, '
        'even if that passage does not answer. Answers in other lectures can be missed by this diagnostic. Counts below use unique queries from round 1, '
        'while latency uses all three rounds. Negative candidates are excluded from source-match denominators.', '',
        '| Method | All source matches /450 | Development /135 | Reserved /315 | p50 | p95 | Errors /1500 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for m in modes:
        cells = [f"{diagnostic[s][m]['authoring_video_hits']} ({diagnostic[s][m]['authoring_video_rate']:.1%})" for s in ['all','development','reserved_evaluation']]
        lat = summary['latency'][m]
        lines.append('| '+ ' | '.join([m]+cells+[f"{lat['p50_ms']:.1f} ms",f"{lat['p95_ms']:.1f} ms",str(lat['errors'])])+' |')
    lines += ['', '## Query style: source matches only', '', '| Style | Queries | Semantic | Elasticsearch | Hybrid | Tuned hybrid |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    for t,group in per_type.items():
        lines.append('| '+ ' | '.join([t,str(group[modes[0]]['queries'])]+[str(group[m]['source_hits']) for m in modes])+' |')
    lines += ['', '## What this run establishes', '',
        '- Lexical queries: Elasticsearch finds the authoring lecture for 142/150 versus semantic139/150. Paraphrases: current hybrid117/150 versus semantic113/150 and lexical92/150. Conceptual: semantic130/150 versus hybrid126/150 and lexical117/150. These are lecture diagnostics, not verified answers.',
        '- Across450 answer-seeking queries, lexical recovers27 source-lecture matches that semantic misses; semantic recovers58 that lexical misses. Current hybrid recovers25 semantic misses but loses27 semantic successes. There is complementary evidence, but naive fusion does not retain it consistently.',
        '- The tuned candidate has the same top-five membership as semantic in500/500 queries and the same order in292/500. This is structurally expected: its minimum semantic-only contribution is0.75/65, greater than the maximum lexical-only contribution0.25/61. With five semantic hits, no lexical-only passage can enter the top five. It can reorder semantic hits, not expand coverage.',
        '- Therefore do not claim the tuned candidate independently validates better recall or answer coverage. It adds lexical retrieval work without expanding the returned set under this configuration.', '']
    lines += ['', '## Existing selected answer-quality evidence', '',
        'These are **not all500 quality scores**. They reuse 30 human-verified and 311 AI-draft passage judgments for the earlier selected development subset. '
        'No previously unknown passage was automatically assigned zero. Full-answer metrics below concern only fully assessed answer-seeking queries.', '',
        '| Method | Assessed queries | Complete-answer Hit@5 | MRR@5 | Pooled nDCG@5 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for m,q in summary['existing_selected_quality'].items():
        lines.append(f"| {m} | {q['queries']} | {q['answer_hit5']:.1%} | {q['answer_mrr5']:.3f} | {q['pooled_ndcg5']:.3f} |")
    lines += ['', '## No-answer candidates and review coverage', '']
    for m in modes:
        n=diagnostic['all'][m]
        lines.append(f"- {m}: {n['candidates_returning_results']}/{n['no_answer_candidates']} negative-candidate queries returned results. This is not an adjudicated false-positive rate.")
    lines += ['', f"The new deduplicated review pool contains {len(pool):,} query/passage pairs. Provenance counts: "
              +json.dumps(summary['review_pool_provenance'])+'. Unknown judgments remain blank. '
              'The earlier user agreement applies only to the original 30 pairs, not the new pool.', '',
        '## Measurement and limits', '',
        '- Same pinned all-MiniLM-L6-v2 CPU model, standard Elasticsearch analyzer, and canonical caption passages as development.',
        '- Baseline hybrid: depth50 per branch, equal-weight RRF k60. Tuned candidate: depth5, 75% semantic /25% lexical, RRF k60.',
        '- Direct localhost backend timings include query embeddings where required; they exclude application API, authorization, transcription, ingress, and concurrent-user load.',
        '- Three rounds do not provide 1,500 independent questions per method. Rank stability: '+str(stability)+'/4000 repeated rankings match round1.',
        '- Timings are from a shared local development machine, not isolated hardware; a regression-test process overlapped part of the run. Small latency differences should not be treated as precise deployment guarantees.',
        '- No production activation, cloud cost, model download, or failure/load extrapolation. Temporary indexes/collection were deleted.',
        '- The historical 100-query timestamp-window results use a different corpus and scoring rule; do not average them into these figures.', '',
        '## Decision', '',
        'Keep the current production behavior. Elasticsearch remains the local speed reference; semantic remains the selected-subset answer-quality reference. '
        'Tuned hybrid is a validation candidate, not a demonstrated overall winner. To make a defensible final quality choice, assess the reserved pooled passages '
        'under the frozen rubric, verify those labels, and compute split-specific complete-answer Hit@5, MRR and graded nDCG. '
        'Do not tune on reserved results and continue calling them a blind test.', '',
        '## Artifacts', '',
        '- `full500_frozen_plan.json`: configuration fixed before execution.',
        '- `full500_results.json`: all6000 request results and cleanup evidence.',
        '- `full500_summary.json`: split metrics, review coverage, provenance and source hashes.',
        '- `full500_review_pool.csv`: deduplicated returned passages with existing labels retained; unjudged cells blank.', '',
        'Reproduce reporting with `.venv/Scripts/python.exe -m benchmarks.summarize_full500`.', '']
    (ROOT/'full500_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['split_diagnostics','latency','review_pool_provenance','repeat_rank_stability']},indent=2))


if __name__ == '__main__':
    main()
