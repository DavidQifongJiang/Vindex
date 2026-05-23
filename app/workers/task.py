from app.workers.celery_app import celery_app
from app.services.video_service import process_video


@celery_app.task(name="process_video_task")
def process_video_task(video_id: str):
    process_video(video_id)