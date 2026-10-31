"""Consolidate existing experiments without pooling incompatible quality metrics."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'library_queries_500'


def main():
    files = {
        'historical': ROOT/'library_phase5_followup.json',
        'smoke': OUT/'development_smoke_results.json',
        'timing': OUT/'comparison_latency_results.json',
        'judged': OUT/'comparison_results.json',
        'fusion': OUT/'comparison_fusion_results.json',
    }
    data = {key:json.loads(path.read_text(encoding='utf-8')) for key,path in files.items()}
    old, smoke, timing, judged, fusion = [data[k] for k in files]
    full_path = OUT/'full500_summary.json'
    full = json.loads(full_path.read_text(encoding='utf-8')) if full_path.exists() else None
    old_queries = {r['label_id'] for r in old['rows']}
    new_queries = {r['query_id'] for r in smoke['rows']}
    assessed_queries = {r['query_id'] for r in judged['quality_rows']+judged['negative_rows']}
    assert len(old_queries)==100 and len(new_queries)==150 and len(assessed_queries)==30
    assert assessed_queries <= new_queries
    assert {r['query_id'] for r in timing['rows']} == new_queries
    assert {r['query_id'] for r in fusion['rows']} == assessed_queries
    inventory = {
        'historical_fixture_queries':100,
        'new_development_queries_executed':150,
        'new_reserved_queries_executed':350 if full else 0,
        'new_reserved_queries_unexecuted':0 if full else 350,
        'pooled_assessed_queries_subset_of_development':30,
        'human_verified_passage_pairs':30,
        'ai_draft_passage_pairs':311,
        'smoke_requests':len(smoke['rows']),
        'repeat_timing_requests':len(timing['rows']),
        'full500_requests':full['requests'] if full else 0,
        'note':'Repeated requests, methods, and overlapping subsets are not additional independent queries.',
    }
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), inventory=inventory,
        source_sha256={k:sha256(p.read_bytes()).hexdigest() for k,p in files.items()},
        historical_timestamp_window_metrics=old['summary'],
        development_source_video_diagnostics=smoke['summary'],
        selected_passage_quality=judged['quality'], quality_provenance=judged['provenance'],
        repeated_backend_latency=judged['latency'], context_ablation=judged['context'],
        fusion_ablation=fusion['summaries'], historical_failure_gates=old['failure_gates'],
        conclusion='No overall quality winner established. Elasticsearch has the lowest measured latency; semantic leads complete-answer coverage on the selected difficult development subset; current hybrid has not demonstrated incremental quality benefit sufficient for promotion.',
        limits=['Historical and new corpora, scoring criteria, and request paths differ: do not average their quality or latency.',
                'Selected 30-query assessment is not representative; 311 new passage ratings are AI drafts.',
                'Only 30 original passage ratings are human-verified; agreement does not extend automatically to new labels.',
                'No held-out results or new rollout authorization are implied.'])
    if full:
        report['full500_comparison'] = full
        report['source_sha256']['full500_summary'] = sha256(full_path.read_bytes()).hexdigest()
        report['limits'][-1] = 'Reserved execution is complete, but reserved answer-accuracy labels are missing; no rollout is implied.'
    (OUT/'consolidated_evidence.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines = ['# Consolidated search evidence', '',
        '**Conclusion: no overall quality winner established.** Elasticsearch is the speed leader in the local runs; '
        'semantic leads complete-answer coverage on the selected difficult subset. Current hybrid has not shown '
        'enough incremental benefit to become the default.', '',
        '## Count reconciliation', '',
        '- Historical fixture: 100 queries, reused in its follow-up run.',
        ('- New dataset: all500 queries executed, including150 development and350 reserved; see the [full report](full500_report.md).'
         if full else '- New dataset: 500 queries; 150 development queries executed, 350 reserved queries unexecuted.'),
        '- Passage assessment: 30 of those same 150 queries, not 30 additional queries.',
        '- Judgments: 341 passage pairs, comprising 30 human-verified and 311 AI drafts.',
        '- Repeated timing: 1,350 requests = 150 queries × 3 methods × 3 rounds.',
        '- Fusion replay: the same 30 assessed queries under three configurations.',
        '- Counts must not mix queries, passages, methods, or repetitions.', '',
        '## Comparable results kept within their experiments', '',
        '| Evidence | Semantic | Elasticsearch | Hybrid | Meaning |',
        '| --- | ---: | ---: | ---: | --- |']
    modes = ['semantic','lexical','hybrid']
    lines.append('| Historical 100-query Hit@5 | '+' | '.join(f"{old['summary'][m]['hit_at_5']:.0%}" for m in modes)+' | Reused timestamp-window labels; ten 15-minute clips |')
    lines.append('| New 135-query authoring-lecture inclusion | '+' | '.join(f"{smoke['summary'][m]['authoring_video_in_top5_rate']:.1%}" for m in modes)+' | Source-video diagnostic, not answer accuracy |')
    lines.append('| Selected 25 answer-seeking queries, complete-answer Hit@5 | '+' | '.join(f"{judged['quality'][m]['answer_hit5']:.0%}" for m in modes)+' | 30 human + 311 AI passage judgments across 30 questions including 5 negatives |')
    lines.append('| Repeated local backend p95 | '+' | '.join(f"{judged['latency'][m]['p95_ms']:.1f} ms" for m in modes)+' | 450 requests per method; excludes full application API |')
    lines += ['', 'Do not average the rows: corpus, relevance rule, denominator, and timing path differ.', '',
        '## Experimental interpretation', '',
        '- Elasticsearch: strong speed evidence; historical fixture favors its lexical matching, but the difficult natural-language subset exposes incidental-word matches.',
        '- Semantic: best complete-answer coverage on the selected subset; still many incomplete or wrong-topic passages and no validated abstention behavior.',
        '- Hybrid: combining rankings can push out useful answers. Depth-5 fusion with 75% semantic weight recovers 24% complete-answer Hit@5 on the selected subset but does not beat semantic alone.',
        '- Adding adjacent context: 0/8 complete answers before and after, with 3.58× as many words. Longer snippets alone are not a demonstrated fix.',
        '- All five assessed negative candidates receive only grade-0 passages from every baseline method; this does not prove full-library unanswerability.', '',
        '## Decision', '',
        ('All500 execution is complete; keep configurations unchanged pending reserved passage relevance review. ' if full else
         'Keep configurations unchanged pending a clearly identified final query set and complete relevance review. ')+
        'Use semantic as the current development quality reference and Elasticsearch as the latency reference. '
        'Validate a frozen tuned-hybrid candidate on reserved queries before selecting a default. '
        'The roadmap still requires accepted-transcript/API and deployment-specific checks before rollout.', '',
        'See [detailed experiments](comparison_report.md) and `consolidated_evidence.json` for source hashes and metrics.', '']
    (OUT/'consolidated_evidence.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(inventory,indent=2))


if __name__ == '__main__':
    main()
