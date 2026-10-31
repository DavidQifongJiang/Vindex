"""Development-query smoke test on official captions; no relevance labels implied."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import random
import re
from time import perf_counter
from uuid import NAMESPACE_URL, uuid4, uuid5

import numpy as np
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.projection_indexes import ElasticsearchProjectionIndex, QdrantProjectionIndex
from app.services.search_contracts import TranscriptSnapshot
from benchmarks.replay_development_fusion import fuse

ROOT = Path(__file__).resolve().parent
REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"


def seconds(value):
    parts = [float(p) for p in value.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def passages(path):
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            if " --> " not in line:
                continue
            start, end = line.split(" --> ")
            text = unescape(re.sub(r"<[^>]+>", "", " ".join(lines[i + 1:])))
            if text.strip():
                cues.append(dict(start=seconds(start.strip()), end=seconds(end.split()[0]), text=text.strip()))
            break
    result, current = [], None
    for cue in cues:
        if current is None or cue['end'] - current['start'] > 30:
            if current:
                result.append(current)
            current = dict(cue)
        else:
            current['end'] = cue['end']
            current['text'] += ' ' + cue['text']
    if current:
        result.append(current)
    if not result:
        raise ValueError(f"No captions parsed: {path}")
    return result


def summarize(rows):
    valid = [r for r in rows if r.get('error') is None]
    positive = [r for r in valid if r['authoring_video_key']]
    negative = [r for r in valid if not r['authoring_video_key']]
    return dict(requests=len(rows), errors=len(rows)-len(valid),
        p50_ms=float(np.percentile([r['latency_ms'] for r in valid], 50)) if valid else None,
        p95_ms=float(np.percentile([r['latency_ms'] for r in valid], 95)) if valid else None,
        authoring_video_in_top5_count=sum(r['authoring_video_in_top5'] for r in positive),
        authoring_video_query_count=len(positive),
        authoring_video_in_top5_rate=sum(r['authoring_video_in_top5'] for r in positive)/len(positive) if positive else None,
        negative_candidates_returning_results=sum(bool(r['results']) for r in negative),
        negative_candidate_count=len(negative))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qdrant-url', required=True)
    parser.add_argument('--elasticsearch-url', default='http://127.0.0.1:9200')
    parser.add_argument('--output', type=Path, default=ROOT / 'library_queries_500' / 'development_smoke_results.json')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--all-queries', action='store_true', help='Explicitly execute the reserved queries too, with frozen tuned-hybrid candidate')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    query_path = ROOT / 'library_queries_500' / ('queries.csv' if args.all_queries else 'development_150.csv')
    with query_path.open(encoding='utf-8', newline='') as handle:
        queries = list(csv.DictReader(handle))
    if not args.all_queries and (len(queries) != 150 or any(q['split'] != 'development' for q in queries)):
        raise ValueError('This smoke runner accepts only the 150 development queries')
    if args.all_queries and (len(queries) != 500 or Counter(q['split'] for q in queries) != {'development':150, 'reserved_evaluation':350}):
        raise ValueError('Full run must contain exactly 150 development and 350 reserved queries')
    configured_modes = ['semantic', 'lexical', 'hybrid'] + (['hybrid_tuned'] if args.all_queries else [])
    if args.all_queries:
        plan_path = ROOT/'library_queries_500/full500_frozen_plan.json'
        frozen = json.loads(plan_path.read_text())
        if frozen['query_sha256'] != sha256(query_path.read_bytes()).hexdigest() or frozen['repetitions'] != args.repeats:
            raise ValueError('Full run does not match the frozen plan')
    sources = json.loads((ROOT / 'lecture_captions' / 'manifest.json').read_text())['captions']
    documents, video_keys, snapshots = [], {}, []
    for source in sources:
        path = ROOT.parent / source['file_path']
        if sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Caption hash mismatch')
        video_id = str(uuid5(NAMESPACE_URL, source['video_key']))
        revision = str(uuid5(NAMESPACE_URL, source['sha256'] + ':caption-30s-v1'))
        snapshot = TranscriptSnapshot.from_manifest(video_id, revision, json.dumps(passages(path)).encode())
        snapshots.append(snapshot)
        documents.extend(p.as_hit() for p in snapshot.passages)
        video_keys[video_id] = source['video_key']
    target = 'vindex-dev-smoke-' + uuid4().hex
    attempt = str(uuid4())
    elastic = ElasticsearchProjectionIndex(target, args.elasticsearch_url)
    client = QdrantClient(url=args.qdrant_url, check_compatibility=False, timeout=60)
    vector_index = QdrantProjectionIndex(target, 384, client)
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), query_count=len(queries),
        videos=len(snapshots), passages=len(documents), query_sha256=sha256(query_path.read_bytes()).hexdigest(),
        caption_hashes={s['video_key']: s['sha256'] for s in sources},
        corpus_sha256=sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest(),
        model='all-MiniLM-L6-v2', model_revision=REVISION, candidates=50, rrf_k=60,
        chunking='Consecutive complete caption cues up to 30 seconds, no overlap; includes introductory captions',
        measurement='Direct backend retrieval on localhost, warm CPU model; hybrid branches parallel; randomized rounds',
        repetitions=args.repeats,
        configured_modes=configured_modes,
        split_counts=dict(Counter(q['split'] for q in queries)),
        limitations=[
            'No human relevance labels: authoring-video inclusion is a diagnostic, NOT answer accuracy, recall, or Hit@5.',
            'Other lectures can be relevant; the authoring lecture can contain non-answer passages.',
            'No-answer candidates are unreviewed; result presence is not a verified false-positive rate.',
            'Official captions and 30-second chunks, not application-ingested accepted transcripts.',
            'Bypasses API, PostgreSQL publication/auth checks, ingress, transcription, and concurrent user load.',
            'All 500 queries executed without post-reserved tuning.' if args.all_queries else 'Development queries only; 350 reserved queries not executed. No tuning performed.',
            'Latency includes per-query embedding when needed, but excludes model load and corpus indexing.',
        ], rows=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report['elasticsearch_version'] = elastic.request('GET', '')['version']['number']
        report['qdrant_version'] = client.info().version
        start = perf_counter()
        model = SentenceTransformer('all-MiniLM-L6-v2', revision=REVISION, local_files_only=True, device='cpu')
        if model[0].auto_model.config._commit_hash != REVISION:
            raise ValueError('Model revision mismatch')
        report['model_load_seconds'] = perf_counter()-start
        start = perf_counter()
        for snapshot in snapshots:
            embeddings = model.encode([p.text for p in snapshot.passages], normalize_embeddings=True, batch_size=32).tolist()
            elastic.write(snapshot, attempt)
            vector_index.write(snapshot, attempt, embeddings)
            print('Indexed', video_keys[snapshot.video_id], len(snapshot.passages), flush=True)
        report['index_setup_seconds'] = perf_counter()-start
        if elastic.request('POST', target+'/_count')['count'] != len(documents) or client.count(target, exact=True).count != len(documents):
            raise ValueError('Indexed count mismatch')
        report['indexed_counts_verified'] = True
        report['elasticsearch_store_bytes'] = elastic.request('GET', target+'/_stats/store')['_all']['total']['store']['size_in_bytes']
        report['qdrant_collection'] = client.get_collection(target).model_dump(mode='json')
        def semantic(query, limit=50):
            vector = model.encode(query, normalize_embeddings=True).tolist()
            result = client.query_points(target, query=vector, limit=limit, with_payload=True).points
            return [dict(p.payload, score=p.score) for p in result]
        def lexical(query, limit=50):
            return elastic.search(attempt, query, limit)
        with ThreadPoolExecutor(max_workers=2) as pool:
            def retrieve(mode, query):
                if mode == 'semantic':
                    return semantic(query)[:5]
                if mode == 'lexical':
                    return lexical(query)[:5]
                depth = 5 if mode == 'hybrid_tuned' else 50
                dense = pool.submit(semantic, query, depth)
                sparse = pool.submit(lexical, query, depth)
                if mode == 'hybrid_tuned':
                    return fuse(dense.result(), sparse.result(), .75, k=60)
                return reciprocal_rank_fusion(dense.result(), sparse.result(), limit=5, k=60)
            for mode in configured_modes:
                retrieve(mode, queries[0]['query'])
            rng = random.Random(20260916)
            order = []
            for round_index in range(args.repeats):
                batch = list(queries)
                rng.shuffle(batch)
                order.extend((round_index, q) for q in batch)
            for i, (round_index, query) in enumerate(order):
                modes = list(configured_modes)
                rng.shuffle(modes)
                for mode in modes:
                    start = perf_counter()
                    row = {key: query[key] for key in ['query_id', 'query_type', 'query', 'authoring_video_key', 'split']}
                    row['mode'] = mode
                    row['round'] = round_index
                    try:
                        results = retrieve(mode, query['query'])
                        row['latency_ms'] = (perf_counter()-start)*1000
                        row['results'] = [dict(r, video_key=video_keys[r['video_id']]) for r in results]
                        row['authoring_video_in_top5'] = any(r['video_key'] == query['authoring_video_key'] for r in row['results']) if query['authoring_video_key'] else None
                    except Exception as exc:
                        row.update(error=f'{type(exc).__name__}: {exc}', latency_ms=(perf_counter()-start)*1000, results=[])
                    report['rows'].append(row)
                if (i+1) % 25 == 0:
                    print('Queried', i+1, '/', len(order), flush=True)
        report['summary'] = {m: summarize([r for r in report['rows'] if r['mode'] == m]) for m in configured_modes}
        report['by_split'] = {s:{m:summarize([r for r in report['rows'] if r['mode']==m and r['split']==s]) for m in configured_modes} for s in sorted({q['split'] for q in queries})}
        report['by_type'] = {t: {m: summarize([r for r in report['rows'] if r['mode'] == m and r['query_type'] == t]) for m in report['summary']} for t in sorted({q['query_type'] for q in queries})}
    finally:
        cleanup = {}
        for name, operation in [('elasticsearch', lambda: elastic.request('DELETE', target)),
                                ('qdrant', lambda: client.delete_collection(target))]:
            try:
                operation()
                cleanup[name] = 'deleted'
            except Exception as exc:
                cleanup[name] = f'{type(exc).__name__}: {exc}'
        report['cleanup'] = cleanup
        report['temporary_target'] = target
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        elastic.close()
        client.close()
    print(json.dumps(report.get('summary', {}), indent=2))


if __name__ == '__main__':
    main()
