"""Run the production Celery wrapper with a cheap processor in isolated integration tests."""
from app.db.models import VideoSearchChunk
from app.db.pipeline_repository import complete_work
from app.db.session import create_session
from app.workers.celery_app import celery_app as app
from app.workers import task


def process_probe(video_id, chunk_index, *, claimed_chunk, lease):
    db = create_session()
    try:
        complete_work(db, VideoSearchChunk, video_id, chunk_index, claimed_chunk.processing_token)
    finally:
        db.close()
    return {"video_id": video_id, "status": "processed"}


task.process_video_search_chunk = process_probe
