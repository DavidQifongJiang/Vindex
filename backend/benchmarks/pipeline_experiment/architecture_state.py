"""Integrity verification for both retained whole-video and current DAG publications."""
import argparse
import hashlib
import json
import os
from math import isclose
from pathlib import Path

from guard import validate_environment


def completed_search_publication(video):
    if video.embedding_status != 'completed':
        return False
    assert video.segments_path and video.embedding_path, 'Completed search publication lacks artifacts'
    return True


def validate_legacy_cardinality(segments, embeddings, points):
    assert len(segments) == len(embeddings) == len(points), 'Incomplete legacy search publication'
    identity = lambda rows: sorted((r['text'], r['start'], r['end']) for r in rows)
    # JSON/Qdrant may round the last floating-point bit (108.39999999999999 ->
    # 108.4). Preserve every segment and exact text, allowing only 1 ns in time.
    for original, embedding, indexed in zip(identity(segments), identity(embeddings), identity([p.payload for p in points])):
        assert original[0] == embedding[0] == indexed[0]
        assert all(isclose(original[i], other[i], rel_tol=0, abs_tol=1e-9)
                   for other in (embedding, indexed) for i in (1, 2))


def main():
    validate_environment(os.environ)
    from app.db.models import Video, PipelineJob, VideoSearchChunk, UploadRequest
    from app.db.session import create_session
    from app.services.storage_service import get_storage
    from app.services.qdrant_service import get_qdrant_client, COLLECTION_NAME
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from mixed_state import validate_ids
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    spec = json.loads(args.manifest.read_text())
    expected, seeds = spec['expected'], spec['seeds']
    validate_ids([r['video_id'] for r in expected], seeds)
    assert spec['architecture'] in ('dag', 'branches', 'serialized')
    db, storage, qdrant = create_session(), get_storage(), get_qdrant_client()
    try:
        videos = db.query(Video).filter(~Video.video_id.in_(seeds)).all()
        assert {v.video_id for v in videos} == {r['video_id'] for r in expected}
        fixtures = {r['video_id']: r for r in expected}
        verified = []
        for video in videos:
            vid, fixture = video.video_id, fixtures[video.video_id]
            reservations = db.query(UploadRequest).filter_by(video_id=vid).all()
            jobs = db.query(PipelineJob).filter_by(video_id=vid).all()
            chunks = db.query(VideoSearchChunk).filter_by(video_id=vid).all()
            assert len(reservations) == 1 and reservations[0].status == 'completed'
            if spec['architecture'] != 'serialized':
                assert {'playback', 'search'} <= {j.stage for j in jobs}
            else:
                assert not jobs and not chunks, 'Legacy control unexpectedly ran the DAG'
            digest, size = hashlib.sha256(), 0
            body = storage.client.get_object(Bucket=storage.bucket, Key=video.raw_path)['Body']
            try:
                for part in body.iter_chunks(chunk_size=1048576):
                    digest.update(part); size += len(part)
            finally:
                body.close()
            assert size == fixture['bytes'] and digest.hexdigest() == fixture['sha256']
            row = {'video_id': vid, 'kind': fixture['kind'], 'bytes': size,
                   'sha256': digest.hexdigest(), 'job_count': len(jobs), 'chunk_count': len(chunks),
                   'status': video.status, 'embedding_status': video.embedding_status,
                   'failed_jobs': sum(j.status == 'failed' for j in jobs + chunks)}
            if completed_search_publication(video):
                points, offset = qdrant.scroll(COLLECTION_NAME,
                    scroll_filter=Filter(must=[FieldCondition(key='video_id', match=MatchValue(value=vid))]),
                    with_payload=True, with_vectors=False, limit=10000)
                assert offset is None and points
                segments = storage.read_json(video.segments_path)
                embeddings = storage.read_json(video.embedding_path)
                assert len(points) == len(embeddings)
                if spec['architecture'] != 'serialized':
                    assert chunks and all(c.status == 'completed' and c.artifact_token for c in chunks)
                    assert len(points) == sum(c.segment_count for c in chunks)
                    assert all(p.payload.get('attempt_token') in {c.artifact_token for c in chunks} for p in points)
                else:
                    validate_legacy_cardinality(segments, embeddings, points)
                    assert all(not p.payload.get('attempt_token') for p in points)
                assert segments and all(s['end'] > s['start'] >= 0 for s in segments)
                if fixture['kind'] == 'sentinel':
                    assert max(s['end'] for s in segments) >= 850, 'Long fixture did not exercise its full duration'
                    if spec['architecture'] != 'serialized':
                        assert len(chunks) == (1 if spec['architecture'] == 'branches' else 3)
                        assert all(c.segment_count > 0 for c in chunks)
                import requests
                response = requests.post(f'http://127.0.0.1:8000/videos/{vid}/search',
                    cookies={'vindex_bench': os.environ['VINDEX_BENCH_TOKEN']},
                    json={'query': 'vector search', 'algorithm': 'qdrant_embedding'}, timeout=30)
                assert response.status_code == 200 and response.json()['video_id'] == vid
                assert any('vector search' in hit['text'].lower() and 0 <= hit['start'] < hit['end']
                           for hit in response.json()['results'])
                row.update(vectors=len(points), semantic_probe_pass=True,
                           last_segment_end=max(s['end'] for s in segments))
            if video.status == 'processed':
                assert video.processed_path
                storage.client.head_object(Bucket=storage.bucket, Key=video.processed_path)
            verified.append(row)
        print(json.dumps({'architecture': spec['architecture'], 'verified': verified}))
    finally:
        db.close()


if __name__ == '__main__':
    main()
