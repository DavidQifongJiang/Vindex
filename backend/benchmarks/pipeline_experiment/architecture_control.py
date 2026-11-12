"""Benchmark-only routing to the retained legacy processor; never changes app source."""
import json
import os
from pathlib import Path

from guard import validate_environment

CONTROL = Path('/bench/architecture.json')


def architecture():
    validate_environment(os.environ)
    value = json.loads(CONTROL.read_text())['architecture']
    if value not in ('serialized', 'branches', 'dag'):
        raise ValueError('Unknown experiment architecture')
    return value


def install_upload_adapter():
    validate_environment(os.environ)
    from app.db import upload_repository
    original = upload_repository.create_video

    def create_video(db, metadata, *, enqueue_processing=False):
        return original(db, metadata, enqueue_processing=(enqueue_processing and architecture() != 'serialized'))

    upload_repository.create_video = create_video


def configure_worker():
    # One whole-video search work unit in B; five-minute units in C/D. Identical
    # durable publication/lease code makes B -> C a chunk-size-only intervention.
    value = architecture()
    os.environ['SEARCH_CHUNK_SECONDS'] = '1800' if value == 'branches' else '300'
    return {'architecture': value, 'chunk_seconds': int(os.environ['SEARCH_CHUNK_SECONDS'])}


def dispatch_legacy(db, publish):
    from datetime import datetime
    from sqlalchemy import and_, or_
    from app.db.models import Video, PipelineJob
    rows = db.query(Video.video_id).filter(
        or_(Video.status == 'uploaded', and_(Video.status == 'retrying', Video.next_retry_at <= datetime.utcnow())),
        ~db.query(PipelineJob).filter(PipelineJob.video_id == Video.video_id).exists(),
    ).order_by(Video.created_at).limit(100).all()
    db.rollback()
    for (video_id,) in rows:
        publish('process_video_task', args=[video_id], queue='celery')
    return len(rows)


def main():
    import time
    from app.db.session import create_session
    from app.workers.celery_app import celery_app
    from app.workers.pipeline_dispatcher import recover_expired, dispatch_pending
    validate_environment(os.environ)
    while True:
        db = create_session()
        try:
            publish = lambda name, **kwargs: celery_app.send_task(name, retry=False, **kwargs)
            if architecture() == 'serialized':
                dispatch_legacy(db, publish)
            else:
                recover_expired(db)
                dispatch_pending(db, publish)
        finally:
            db.close()
        time.sleep(5)


if __name__ == '__main__':
    main()
