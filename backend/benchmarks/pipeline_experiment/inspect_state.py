"""Executed inside the isolated API container, outside timed browser waves."""
import argparse
import hashlib
import json
import os
from datetime import datetime

from guard import validate_environment


def main():
    validate_environment(os.environ)
    from app.db.models import Video, PipelineJob, VideoSearchChunk, UploadRequest
    from app.db.session import create_session
    from app.services.storage_service import get_storage
    from app.services.qdrant_service import get_qdrant_client, COLLECTION_NAME
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['snapshot', 'verify', 'cleanup-residuals'])
    parser.add_argument('--expected', default='[]')
    parser.add_argument('--sha256')
    parser.add_argument('--bytes', type=int)
    args = parser.parse_args()
    expected = json.loads(args.expected)
    db = create_session()
    storage = get_storage()
    def rows(model, excluded=()):
        return [{c.name:getattr(row,c.name) for c in model.__table__.columns if c.name not in excluded} for row in db.query(model).all()]
    report = {'videos':rows(Video, ['error_message','last_error_message']),
              'jobs':rows(PipelineJob,['last_error_message']), 'chunks':rows(VideoSearchChunk,['last_error_message']),
              'uploads':rows(UploadRequest,['response','fingerprint','idempotency_key','owner_user_id'])}
    report['active'] = sum(row['status'] in ['pending','processing','retrying'] for key in ['jobs','chunks'] for row in report[key])
    if args.action == 'verify':
        assert len(expected) == len(set(expected)) and 0 < len(expected) <= 10
        assert {v['video_id'] for v in report['videos']} == set(expected), 'Unexpected logical videos'
        assert report['active'] == 0, 'Workers have not settled'
        verified = []
        qdrant = get_qdrant_client()
        for video in report['videos']:
            vid = video['video_id']
            jobs = [j for j in report['jobs'] if j['video_id'] == vid]
            chunks = [j for j in report['chunks'] if j['video_id'] == vid]
            reservations = [u for u in report['uploads'] if u['video_id'] == vid]
            assert video['status'] == 'processed' and video['embedding_status'] == 'completed' and video['processed_path']
            assert jobs and all(j['status'] == 'completed' for j in jobs)
            assert chunks and all(c['status'] == 'completed' and c['artifact_token'] for c in chunks)
            assert len(reservations) == 1 and reservations[0]['status'] == 'completed'
            digest, size = hashlib.sha256(), 0
            body = storage.client.get_object(Bucket=storage.bucket, Key=video['raw_path'])['Body']
            try:
                for part in body.iter_chunks(chunk_size=1048576): digest.update(part); size += len(part)
            finally: body.close()
            assert digest.hexdigest() == args.sha256 and size == args.bytes, 'Accepted input mismatch'
            points, offset = qdrant.scroll(COLLECTION_NAME, scroll_filter=Filter(must=[FieldCondition(key='video_id',match=MatchValue(value=vid))]),
                                          with_payload=True, with_vectors=False, limit=10000)
            assert offset is None, 'Vector verification requires pagination'
            tokens = {c['artifact_token'] for c in chunks}
            assert points and all(p.payload.get('attempt_token') in tokens for p in points), 'Stale/unpublished vectors'
            assert len(points) == sum(c['segment_count'] for c in chunks), 'Vector cardinality mismatch'
            verified.append({'video_id':vid,'sha256':digest.hexdigest(),'bytes':size,'vectors':len(points),'job_count':len(jobs),
                             'chunk_count':len(chunks),'single_attempt':all(j['processing_attempt_count']==1 for j in jobs+chunks)})
        report['verified'] = verified
    elif args.action == 'cleanup-residuals':
        # Only after application deletion, no active work, and a recorded successful verification.
        assert not report['videos'] and not report['jobs'] and not report['chunks'] and report['active'] == 0
        assert expected and len(expected) <= 10
        for vid in expected:
            from uuid import UUID
            assert str(UUID(vid)) == vid
            storage.delete_prefix(f'raw_videos/{vid}/')
            storage.delete_many([f'metrics/{vid}.json'])
        db.query(UploadRequest).filter(UploadRequest.video_id.in_(expected)).delete(synchronize_session=False)
        db.commit()
        report['remaining_objects'] = storage.client.list_objects_v2(Bucket=storage.bucket, Prefix='raw_videos/').get('KeyCount',0)
        qdrant = get_qdrant_client()
        report['remaining_vectors'] = qdrant.count(COLLECTION_NAME, exact=True).count if qdrant.collection_exists(COLLECTION_NAME) else 0
        assert report['remaining_objects'] == report['remaining_vectors'] == 0
    print(json.dumps(report, default=lambda value:value.isoformat() if isinstance(value,datetime) else str(value)))
    db.close()


if __name__ == '__main__': main()
