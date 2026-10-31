"""Paired retrieval evaluation over complete, frozen per-video segment corpora.

Run with python -m benchmarks.compare_retrieval --help from the repository root.
Offline dense retrieval is exact cosine, NOT a Qdrant server latency benchmark.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import random
from datetime import datetime, timezone
from time import perf_counter

import requests

from app.services.hybrid_search import BM25, CANDIDATES, RRF_K, reciprocal_rank_fusion, segment_key
from app.services.elasticsearch_service import ElasticsearchSearch
from app.services.lexical_index import LexicalIndex
from benchmarks.benchmark_common import percentile


def relevance(result, label):
    start, end = float(label['expected_start_seconds']), float(label['expected_end_seconds'])
    window = float(label.get('relevance_window_seconds') or 30)
    return result['start'] <= end and result['end'] >= start or abs(result['start'] - start) <= window


def evaluate(results, label):
    flags = [relevance(result, label) for result in results[:5]]
    first = next((i for i, flag in enumerate(flags) if flag), None)
    return {'hit_at_1': int(bool(flags) and flags[0]), 'hit_at_5': int(any(flags)),
            'precision_at_5': sum(flags) / 5,
            'mrr': 0 if first is None else 1 / (first + 1),
            'timestamp_error_seconds': None if first is None else abs(results[first]['start'] - float(label['expected_start_seconds']))}


def load_corpus(path):
    corpus = json.loads(path.read_text(encoding='utf-8'))
    videos = {}
    for video in corpus['videos']:
        video = dict(video)
        if 'segments_file' in video:
            video['segments'] = json.loads(Path(video['segments_file']).read_text(encoding='utf-8'))
        segments = [{key: s[key] for key in ('start', 'end', 'text')} for s in video['segments']]
        if any(s['end'] < s['start'] for s in segments):
            raise ValueError('Invalid segment interval')
        if len({segment_key(s) for s in segments}) != len(segments):
            raise ValueError('Duplicate segment identities in corpus')
        if video['video_key'] in videos:
            raise ValueError('Duplicate video key')
        video['segments'] = segments
        videos[video['video_key']] = video
    if not videos:
        raise ValueError('Empty corpus')
    digest = hashlib.sha256(json.dumps(videos, sort_keys=True).encode()).hexdigest()
    return videos, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--algorithms', default='dense,bm25,hybrid')
    parser.add_argument('--only', help='Explicit comma-separated video-key subset; exclusions are recorded')
    parser.add_argument('--model', default='all-MiniLM-L6-v2')
    parser.add_argument('--api-base-url', help='Use actual API modes instead of offline exact cosine')
    parser.add_argument('--token-env', default='VINDEX_BENCHMARK_TOKEN')
    parser.add_argument('--elasticsearch-url')
    parser.add_argument('--prepare-elasticsearch', action='store_true')
    args = parser.parse_args()
    algorithms = args.algorithms.split(',')
    valid = {'dense', 'bm25', 'hybrid', 'elasticsearch_bm25', 'elasticsearch_hybrid'}
    if not algorithms or len(set(algorithms)) != len(algorithms) or not set(algorithms) <= valid:
        parser.error('Unknown or duplicate algorithms')
    videos, digest = load_corpus(args.corpus)
    with args.labels.open(encoding='utf-8-sig', newline='') as stream:
        all_labels = list(csv.DictReader(stream))
    selected = set(args.only.split(',')) if args.only else {label['video_key'] for label in all_labels}
    labels = [label for label in all_labels if label['video_key'] in selected]
    if not labels or selected - videos.keys():
        parser.error('Missing full corpus for selected labels; supply complete segments or an explicit --only subset')
    if len({label['label_id'] for label in labels}) != len(labels):
        parser.error('Duplicate label IDs')
    headers = {'Authorization': 'Bearer ' + os.environ[args.token_env]} if os.getenv(args.token_env) else {}
    elastic_class = LexicalIndex if args.api_base_url else ElasticsearchSearch
    elastic = elastic_class(args.elasticsearch_url) if any(a.startswith('elasticsearch') for a in algorithms) else None
    try:
        if elastic and args.prepare_elasticsearch:
            for key in selected:
                video = videos[key]
                elastic.prepare(video['video_id'], video['revision'], video['segments'])
        bm25 = {key: BM25(videos[key]['segments']) for key in selected}
        model = None
        vectors = {}
        if not args.api_base_url and set(algorithms) & {'dense', 'hybrid', 'elasticsearch_hybrid'}:
            import numpy as np
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(args.model, local_files_only=True)
            for key in sorted(selected):
                vectors[key] = model.encode([s['text'] for s in videos[key]['segments']], normalize_embeddings=True)
            model.encode('warmup', normalize_embeddings=True)
        if args.api_base_url:
            # Refuse comparisons against videos which have changed since export.
            for key in selected:
                video = videos[key]
                response = requests.get(args.api_base_url.rstrip('/') + '/videos/' + video['video_id'] + '/status', headers=headers, timeout=30)
                response.raise_for_status()
                if response.json().get('segments_path') != video['revision']:
                    raise ValueError('API manifest differs from frozen corpus')
        rows = []
        order = random.Random(42)
        for label in labels:
            video = videos[label['video_key']]
            iteration = list(algorithms)
            order.shuffle(iteration)
            for algorithm in iteration:
                start = perf_counter()
                if args.api_base_url:
                    response = requests.post(args.api_base_url.rstrip('/') + '/videos/' + video['video_id'] + '/search',
                        headers=headers, json={'query': label['query'], 'algorithm': 'qdrant_embedding' if algorithm == 'dense' else algorithm}, timeout=120)
                    response.raise_for_status()
                    results = response.json()['results']
                else:
                    dense = []
                    if algorithm in {'dense', 'hybrid', 'elasticsearch_hybrid'}:
                        query = model.encode(label['query'], normalize_embeddings=True)
                        scores = vectors[label['video_key']] @ query
                        dense = [dict(video['segments'][int(i)], score=float(scores[i]))
                                 for i in sorted(range(len(scores)), key=lambda i: (-float(scores[i]), segment_key(video['segments'][i])))[:CANDIDATES]]
                    lexical = []
                    if algorithm.startswith('elasticsearch'):
                        lexical = elastic.search(video['video_id'], video['revision'], label['query'], CANDIDATES)
                    elif algorithm in {'bm25', 'hybrid'}:
                        lexical = bm25[label['video_key']].search(label['query'], CANDIDATES)
                    results = reciprocal_rank_fusion(dense, lexical) if 'hybrid' in algorithm else (dense if algorithm == 'dense' else lexical)[:5]
                elapsed = perf_counter() - start
                rows.append({'label_id': label['label_id'], 'video_key': label['video_key'], 'query': label['query'],
                    'algorithm': algorithm, 'latency_seconds': elapsed, **evaluate(results, label), 'results': results})
        summary = {}
        for algorithm in algorithms:
            group = [row for row in rows if row['algorithm'] == algorithm]
            summary[algorithm] = {metric: sum(row[metric] for row in group) / len(group)
                                  for metric in ('hit_at_1', 'hit_at_5', 'precision_at_5', 'mrr')}
            summary[algorithm].update(p95_latency_seconds=percentile([r['latency_seconds'] for r in group], .95),
                p95_timestamp_error_seconds=percentile([r['timestamp_error_seconds'] for r in group], .95))
        report = {'generated_at': datetime.now(timezone.utc).isoformat(),
            'corpus_sha256': digest, 'labels_sha256': hashlib.sha256(args.labels.read_bytes()).hexdigest(),
            'mode': 'api_end_to_end' if args.api_base_url else 'offline_exact_cosine_warm_model',
            'model': args.model, 'model_revision': getattr(model[0].auto_model.config, '_commit_hash', None) if model is not None else None,
            'elasticsearch_version': elastic.request('GET', '')['version']['number'] if elastic else None,
            'candidate_count': CANDIDATES, 'rrf_k': RRF_K, 'bm25_k1': 1.2, 'bm25_b': .75,
            'query_count': len(labels), 'excluded_labels': [l['label_id'] for l in all_labels if l not in labels],
            'query_types': dict(Counter(l.get('query_type', '') for l in labels)),
            'segment_counts': {key: len(videos[key]['segments']) for key in sorted(selected)},
            'limitations': ['Timestamp-window proxy labels; no held-out parameter tuning performed.',
                'Offline timings exclude corpus loading, document embedding and index construction.',
                'Offline dense results are exact cosine, not a Qdrant ANN or concurrent load benchmark.',
                'API Elasticsearch uses shared-corpus statistics and the standard analyzer; offline Elasticsearch uses per-video snapshots. Rankings are not directly comparable.'],
            'summary': summary, 'rows': rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'query_count': len(labels), 'summary': summary}, indent=2))
    finally:
        if elastic:
            elastic.close()


if __name__ == '__main__':
    main()
