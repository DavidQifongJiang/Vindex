from app.workers.celery_app import celery_app
from app.workers.durable_task import run_durable_task
from app.db.session import create_session
from app.db.video_repository import (
    claim_video_for_processing, record_processing_failure, record_processing_retry,
)
from app.services.processing_errors import (
    StaleProcessingAttempt, TemporaryProcessingError, classify_processing_exception, error_type,
)
from app.services.retry_policy import (
    MAX_PROCESSING_ATTEMPTS, attempts_exhausted, next_retry_at, retry_delay_seconds,
)
from app.services.video_service import (
    finalize_chunked_search_if_ready, prepare_video_search_chunks, process_video,
    process_video_playback_artifacts, process_video_search_chunk,
)


def _task_owner(task):
    request = getattr(task, "request", None)
    return getattr(request, "hostname", None) or getattr(request, "id", None) or "unknown-worker"


def run_processing_task(task, video_id: str):
    """Compatibility handler for the legacy whole-video queue."""
    db = create_session()
    try:
        claimed_video = claim_video_for_processing(
            db, video_id, processing_owner=_task_owner(task),
            max_processing_attempts=MAX_PROCESSING_ATTEMPTS,
        )
    except Exception as exc:
        error = classify_processing_exception(exc, default_message="Could not claim video for processing",
                                              default_error_code="processing_claim_failed")
        failed_attempt_count = getattr(task.request, "retries", 0) + 1
        if isinstance(error, TemporaryProcessingError) and not attempts_exhausted(failed_attempt_count):
            raise task.retry(exc=error, countdown=retry_delay_seconds(failed_attempt_count))
        raise error
    finally:
        db.close()
    if claimed_video is None:
        return {"video_id": video_id, "status": "skipped"}
    token = claimed_video.processing_token
    try:
        process_video(video_id, token)
        return {"video_id": video_id, "status": "processed"}
    except StaleProcessingAttempt:
        return {"video_id": video_id, "status": "stale"}
    except Exception as exc:
        error = classify_processing_exception(exc)
        retryable = isinstance(error, TemporaryProcessingError) and not attempts_exhausted(
            claimed_video.processing_attempt_count or 1,
            claimed_video.max_processing_attempts or MAX_PROCESSING_ATTEMPTS)
        delay = retry_delay_seconds(claimed_video.processing_attempt_count or 1) if retryable else None
        db = create_session()
        try:
            fields = dict(error_code=error.error_code, error_type=error_type(error), error_message=str(error)[:1000])
            if retryable:
                marked = record_processing_retry(db, video_id, token, next_retry_at=next_retry_at(delay), **fields)
            else:
                marked = record_processing_failure(db, video_id, token, **fields)
        finally:
            db.close()
        if marked is None:
            return {"video_id": video_id, "status": "stale"}
        if retryable:
            raise task.retry(exc=error, countdown=delay)
        raise error


@celery_app.task(bind=True, name="process_video_task", max_retries=MAX_PROCESSING_ATTEMPTS - 1)
def process_video_task(self, video_id: str):
    return run_processing_task(self, video_id)


@celery_app.task(bind=True, name="process_video_search_task")
def process_video_search_task(self, video_id: str):
    return run_durable_task(self, video_id, "search", prepare_video_search_chunks)


@celery_app.task(bind=True, name="process_video_search_chunk_task")
def process_video_search_chunk_task(self, video_id: str, chunk_index: int):
    return run_durable_task(self, video_id, "chunk", process_video_search_chunk, chunk_index=chunk_index)


@celery_app.task(bind=True, name="process_video_playback_task")
def process_video_playback_task(self, video_id: str):
    return run_durable_task(self, video_id, "playback", process_video_playback_artifacts)


@celery_app.task(bind=True, name="finalize_video_search_task")
def finalize_video_search_task(self, video_id: str):
    return run_durable_task(self, video_id, "finalize", finalize_chunked_search_if_ready)
