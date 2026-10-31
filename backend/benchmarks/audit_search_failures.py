"""Reproduce label-based failure routing; do not infer absent answers from unknowns."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from benchmarks.score_accepted_candidates import accepted_labels
from benchmarks.candidate_diagnostics import content_key
from app.services.hybrid_search import reciprocal_rank_fusion


def main():
    base = Path('benchmarks/library_queries_500')
    scores_path = base / 'before_after500_per_query.json'
    scores = json.loads(scores_path.read_text())
    lookup = {(r['query_id'], r['mode']): r for r in scores}
    failures = []
    for row in scores:
        if row['mode'] != 'hybrid' or row['query_type'] == 'no_answer_candidate' or row['answer_hit5']:
            continue
        semantic = bool(lookup[row['query_id'], 'semantic']['answer_hit5'])
        lexical = bool(lookup[row['query_id'], 'lexical']['answer_hit5'])
        category = ('both_branch_top5' if semantic and lexical else 'semantic_top5' if semantic
                    else 'lexical_top5' if lexical else 'neither_branch_top5')
        failures.append(dict(query_id=row['query_id'], query_type=row['query_type'], answer_available_in=category))
    capture_path = Path('tmp/search-quality-v2/run-01/results.json')
    capture = json.loads(capture_path.read_text())
    labels = accepted_labels()
    development = []
    for row in capture['rows']:
        if row['round'] != 0 or row['profile'] != 'baseline' or row['query_type'] == 'no_answer_candidate':
            continue
        branches = row['candidates']['rankings']
        union = reciprocal_rank_fusion(branches['semantic'], branches['lexical'], limit=100)
        answers = [i+1 for i, h in enumerate(union) if labels.get(content_key(row['query'], h), {}).get('rating') == 2]
        if not any(i <= 5 for i in answers):
            development.append(dict(query_id=row['query_id'], query=row['query'], known_answer_fusion_ranks=answers,
                                    classification='ranking_loss' if answers else 'unresolved_not_proven_retrieval_miss'))
    report = dict(source_hashes={str(p): sha256(p.read_bytes()).hexdigest() for p in [scores_path, capture_path]},
        full500_failure_counts=dict(Counter(r['answer_available_in'] for r in failures)),
        full500_failures=failures, development_failure_counts=dict(Counter(r['classification'] for r in development)),
        development_failures=development,
        limitation='Accepted baseline labels, including automated-origin labels. Categories identify ranking loss under those labels, not independently verified answer completeness. Unknown candidates are not irrelevant.',
        rrf=dict(k=60, depth=50, worst_dual_engine_score=2/110, best_single_engine_score=1/61,
                 implication='Every dual-list candidate outranks every single-list candidate at equal weights with these depths.'))
    output = Path('tmp/search-quality-v2/failure-audit.json')
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['full500_failure_counts', 'development_failure_counts', 'rrf']}, indent=2))


if __name__ == '__main__':
    main()
