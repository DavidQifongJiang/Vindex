"""Render smoke diagnostics and pool unjudged results for manual review."""
import csv
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parent / 'library_queries_500'


def main():
    report = json.loads((ROOT / 'development_smoke_results.json').read_text(encoding='utf-8'))
    lines = ['# Development sample smoke test', '',
        f"Run: {report['generated_at']}. {report['query_count']} development queries searched "
        f"all {report['videos']} lectures, containing {report['passages']:,} caption passages.", '',
        '**These are retrieval diagnostics, not an accuracy evaluation.** There are no reviewed relevance labels.', '',
        '| Method | Authoring lecture in top 5 (diagnostic only) | p50 ms | p95 ms | Errors | Negative candidates returning results |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for mode, row in report['summary'].items():
        lines.append(f"| {mode} | {row['authoring_video_in_top5_count']}/{row['authoring_video_query_count']} "
            f"({row['authoring_video_in_top5_rate']:.1%}) | {row['p50_ms']:.1f} | {row['p95_ms']:.1f} | "
            f"{row['errors']} | {row['negative_candidates_returning_results']}/{row['negative_candidate_count']} |")
    lines += ['', 'The authoring lecture may contain a non-answer passage; another lecture may contain a valid answer. '
        'Do not interpret this diagnostic as Hit@5, recall, or answer accuracy. Negative candidate result counts '
        'are not adjudicated false-positive rates.', '',
        '## By query style', '', '| Style | Semantic authoring-lecture inclusion | Lexical inclusion | Hybrid inclusion |',
        '| --- | ---: | ---: | ---: |']
    for kind, modes in report['by_type'].items():
        if kind == 'no_answer_candidate':
            continue
        cells = [f"{r['authoring_video_in_top5_count']}/{r['authoring_video_query_count']}" for r in modes.values()]
        lines.append('| ' + ' | '.join([kind] + cells) + ' |')
    lines += ['', '## Measurement and limits', '', report['measurement'], '',
        f"Elasticsearch {report['elasticsearch_version']}; Qdrant {report['qdrant_version']}; "
        f"{report['model']} revision `{report['model_revision']}`.", '',
        f"Index setup: {report['index_setup_seconds']:.1f} seconds including document embeddings. "
        f"Model load: {report['model_load_seconds']:.1f} seconds. Candidate depth 50 per branch; RRF k=60.", '',
        *['- ' + item for item in report['limitations']], '',
        '## Relevance review', '',
        '`development_review_pool.csv` deduplicates and shuffles the top-five passages from all methods, '
        'hiding which method retrieved each passage. Existing review cells are preserved on regeneration. A reviewer should '
        'mark 0 (irrelevant), 1 (partial answer), or 2 (answers the query), checking the lecture context '
        'and searching outside the pool when needed. Do not score blank judgments as irrelevant. '
        'Pooling top-five results cannot by itself establish exhaustive library-wide relevance.', '',
        'The 350 reserved queries were not executed, and no ranking parameters were tuned.', '',
        '## Reproduce', '', 'Start a disposable Qdrant server and use an available local Elasticsearch server:', '',
        '```powershell', "$env:HF_HUB_OFFLINE='1'", "$env:OMP_NUM_THREADS='2'", "$env:MKL_NUM_THREADS='2'",
        '.venv/Scripts/python.exe -m benchmarks.smoke_library_sample --qdrant-url http://127.0.0.1:16335',
        '.venv/Scripts/python.exe -m benchmarks.summarize_library_smoke', '```', '',
        'The runner creates randomly named temporary indexes/collections and attempts cleanup in `finally`. '
        'The server/container lifecycle is managed separately. Cleanup recorded by this run: '
        + json.dumps(report['cleanup']) + '.', '']
    (ROOT / 'development_smoke_report.md').write_text('\n'.join(lines), encoding='utf-8')
    pool = {}
    for row in report['rows']:
        for result in row['results']:
            key = (row['query_id'], result['passage_id'])
            pool[key] = dict(query_id=row['query_id'], query=row['query'], passage_id=result['passage_id'],
                video_key=result['video_key'], start_seconds=result['start'], end_seconds=result['end'],
                text=result['text'], relevance='', reviewer_notes='')
    values = list(pool.values())
    random.Random(20260916).shuffle(values)
    existing_path = ROOT / 'development_review_pool.csv'
    if existing_path.exists():
        with existing_path.open(encoding='utf-8', newline='') as handle:
            previous = {(r['query_id'], r['passage_id']): r for r in csv.DictReader(handle)}
        for row in values:
            old = previous.get((row['query_id'], row['passage_id']))
            if old:
                row['relevance'] = old['relevance']
                row['reviewer_notes'] = old['reviewer_notes']
    with (ROOT / 'development_review_pool.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)
    print(f'Report generated; {len(values)} unique query/passage pairs for review')


if __name__ == '__main__':
    main()
