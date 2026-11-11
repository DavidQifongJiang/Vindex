"""Evidence and bounded teardown for mixed-load trials on isolated services only."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from uuid import UUID

from guard import validate_environment


def validate_ids(expected, seeds):
    assert expected and seeds and len(expected) <= 1000
    assert len(expected) == len(set(expected)) and not set(expected) & set(seeds)
    for value in expected + seeds:
        assert str(UUID(value)) == value


def main():
    validate_environment(os.environ)
    from app.db.models import Video, PipelineJob, VideoSearchChunk, UploadRequest
    from app.db.session import create_session
    from app.services.storage_service import get_storage
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['summary', 'verify', 'retire', 'residual'])
    parser.add_argument('--seeds', default='[]')
    parser.add_argument('--expected', default='[]')
    parser.add_argument('--sha256')
    parser.add_argument('--bytes', type=int)
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    seeds, expected = json.loads(args.seeds), json.loads(args.expected)
    db = create_session()
    videos = db.query(Video).filter(~Video.video_id.in_(seeds)).all()
    jobs = db.query(PipelineJob).filter(~PipelineJob.video_id.in_(seeds)).all()
    chunks = db.query(VideoSearchChunk).filter(~VideoSearchChunk.video_id.in_(seeds)).all()
    active = [r for r in jobs + chunks if r.status in ('pending', 'processing', 'retrying')]
    report = {'observed_epoch': datetime.utcnow().timestamp(), 'videos': len(videos),
              'searchable': sum(v.embedding_status == 'completed' for v in videos),
              'playable': sum(v.status == 'processed' and bool(v.processed_path) for v in videos),
              'active_jobs': len(active), 'failed_jobs': sum(r.status == 'failed' for r in jobs + chunks),
              'oldest_active_seconds': max([(datetime.utcnow()-r.created_at).total_seconds() for r in active] or [0])}
    if args.action == 'summary':
        print(json.dumps(report)); return
    validate_ids(expected, seeds)
    assert all(db.get(Video, seed).embedding_status == 'completed' for seed in seeds), 'Protected corpus is not ready'
    storage = get_storage()
    if args.action in ('verify', 'retire'):
        assert {v.video_id for v in videos} == set(expected), 'Unaccounted logical videos'
    if args.action == 'verify':
        verified = []
        for video in videos:
            vid = video.video_id
            reservations = db.query(UploadRequest).filter_by(video_id=vid).all()
            own_jobs = [j for j in jobs if j.video_id == vid]
            assert len(reservations) == 1 and reservations[0].status == 'completed'
            assert {'playback', 'search'} <= {j.stage for j in own_jobs}, 'Missing durable initial job'
            digest, size = hashlib.sha256(), 0
            body = storage.client.get_object(Bucket=storage.bucket, Key=video.raw_path)['Body']
            try:
                for part in body.iter_chunks(chunk_size=1048576):
                    digest.update(part); size += len(part)
            finally:
                body.close()
            assert size == args.bytes and digest.hexdigest() == args.sha256, 'Accepted bytes differ'
            verified.append({'video_id': vid, 'bytes': size, 'sha256': digest.hexdigest(),
                             'job_count': len(own_jobs), 'status': video.status,
                             'embedding_status': video.embedding_status})
        report['verified'] = verified
    elif args.action == 'retire':
        # Host checks stopped worker processes and archives the complete pre-retirement
        # database first. This is test teardown, NEVER a successful processing result.
        assert args.writers_stopped, 'Explicit stopped-writer evidence is required'
        for row in active:
            row.status = 'failed'; row.processing_token = None; row.processing_owner = None
            row.lease_expires_at = None
        for video in videos:
            if video.status in ('uploaded', 'processing', 'retrying'):
                video.status = 'failed'
        db.commit()
        report['retired_for_teardown'] = expected
    elif args.action == 'residual':
        assert not videos and not jobs and not chunks, 'Application deletion did not finish'
        for vid in expected:
            storage.delete_prefix(f'raw_videos/{vid}/')
            storage.delete_many([f'metrics/{vid}.json'])
        db.query(UploadRequest).filter(UploadRequest.video_id.in_(expected)).delete(synchronize_session=False)
        db.commit()
        report['remaining_nonseed_videos'] = 0
    print(json.dumps(report, default=str))
    db.close()


if __name__ == '__main__':
    main()
