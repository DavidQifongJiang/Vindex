from app.workers.celery_app import celery_app
from app.db.session import create_session
from app.db.video_repository import (
    claim_video_for_processing,
    record_playback_processing_failure,
    record_processing_failure,
    record_processing_retry,
    record_search_chunk_failure,
    record_search_chunk_retry,
    record_search_processing_failure,
)
from app.services.processing_errors import (
    PermanentProcessingError,
    ProcessingError,
    StaleProcessingAttempt,
    TemporaryProcessingError,
    classify_processing_exception,
    error_type,
)
from app.services.retry_policy import (
    MAX_PROCESSING_ATTEMPTS,
    attempts_exhausted,
    next_retry_at,
    retry_delay_seconds,
)
from app.services.video_service import (
    finalize_chunked_search_if_ready,
    prepare_video_search_chunks,
    process_video,
    process_video_playback_artifacts,
    process_video_search_chunk,
)


def _message(error: ProcessingError):
    return str(error)[:1000]


def _task_owner(task):
    request = getattr(task, "request", None)
    hostname = getattr(request, "hostname", None)
    task_id = getattr(request, "id", None)
    return hostname or task_id or "unknown-worker"


def _record_retry(video_id: str, processing_token: str, error: TemporaryProcessingError, delay_seconds: int):
    db = create_session()
    try:
        return record_processing_retry(
            db,
            video_id,
            processing_token,
            error_code=error.error_code,
            error_type=error_type(error),
            error_message=_message(error),
            next_retry_at=next_retry_at(delay_seconds),
        )
    finally:
        db.close()


def _record_failure(video_id: str, processing_token: str, error: ProcessingError):
    db = create_session()
    try:
        return record_processing_failure(
            db,
            video_id,
            processing_token,
            error_code=error.error_code,
            error_type=error_type(error),
            error_message=_message(error),
        )
    finally:
        db.close()


def _record_branch_failure(video_id: str, error: ProcessingError, branch: str):
    db = create_session()
    try:
        record_failure = (
            record_search_processing_failure
            if branch == "search"
            else record_playback_processing_failure
        )
        return record_failure(
            db,
            video_id,
            error_code=error.error_code,
            error_type=error_type(error),
            error_message=_message(error),
        )
    finally:
        db.close()


def _record_chunk_retry(
    video_id: str,
    chunk_index: int,
    error: TemporaryProcessingError,
    delay_seconds: int,
):
    db = create_session()
    try:
        return record_search_chunk_retry(
            db,
            video_id,
            chunk_index,
            error_code=error.error_code,
            error_type=error_type(error),
            error_message=_message(error),
            next_retry_at=next_retry_at(delay_seconds),
        )
    finally:
        db.close()


def _record_chunk_failure(video_id: str, chunk_index: int, error: ProcessingError):
    db = create_session()
    try:
        return record_search_chunk_failure(
            db,
            video_id,
            chunk_index,
            error_code=error.error_code,
            error_type=error_type(error),
            error_message=_message(error),
        )
    finally:
        db.close()


def _retry_without_claim(task, error: TemporaryProcessingError):
    failed_attempt_count = getattr(task.request, "retries", 0) + 1

    if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
        raise error

    delay_seconds = retry_delay_seconds(failed_attempt_count)
    raise task.retry(exc=error, countdown=delay_seconds)


def run_processing_task(task, video_id: str):
    db = create_session()
    try:
        claimed_video = claim_video_for_processing(
            db,
            video_id,
            processing_owner=_task_owner(task),
            max_processing_attempts=MAX_PROCESSING_ATTEMPTS,
        )
    except Exception as exc:
        error = classify_processing_exception(
            exc,
            default_message="Could not claim video for processing",
            default_error_code="processing_claim_failed",
        )
        if isinstance(error, TemporaryProcessingError):
            _retry_without_claim(task, error)
        raise error
    finally:
        db.close()

    if claimed_video is None:
        return {
            "video_id": video_id,
            "status": "skipped",
        }

    processing_token = claimed_video.processing_token

    try:
        process_video(video_id, processing_token)
        return {
            "video_id": video_id,
            "status": "processed",
        }
    except StaleProcessingAttempt:
        return {
            "video_id": video_id,
            "status": "stale",
        }
    except TemporaryProcessingError as exc:
        attempt_count = claimed_video.processing_attempt_count or 1
        max_attempts = claimed_video.max_processing_attempts or MAX_PROCESSING_ATTEMPTS

        if attempts_exhausted(attempt_count, max_attempts):
            marked = _record_failure(video_id, processing_token, exc)
            if marked is None:
                return {
                    "video_id": video_id,
                    "status": "stale",
                }
            raise exc

        delay_seconds = retry_delay_seconds(attempt_count)
        marked = _record_retry(video_id, processing_token, exc, delay_seconds)
        if marked is None:
            return {
                "video_id": video_id,
                "status": "stale",
            }

        raise task.retry(exc=exc, countdown=delay_seconds)
    except PermanentProcessingError as exc:
        marked = _record_failure(video_id, processing_token, exc)
        if marked is None:
            return {
                "video_id": video_id,
                "status": "stale",
            }
        raise
    except Exception as exc:
        error = classify_processing_exception(exc)
        if isinstance(error, TemporaryProcessingError):
            attempt_count = claimed_video.processing_attempt_count or 1
            max_attempts = claimed_video.max_processing_attempts or MAX_PROCESSING_ATTEMPTS

            if attempts_exhausted(attempt_count, max_attempts):
                marked = _record_failure(video_id, processing_token, error)
                if marked is None:
                    return {
                        "video_id": video_id,
                        "status": "stale",
                    }
                raise error

            delay_seconds = retry_delay_seconds(attempt_count)
            marked = _record_retry(video_id, processing_token, error, delay_seconds)
            if marked is None:
                return {
                    "video_id": video_id,
                    "status": "stale",
                }
            raise task.retry(exc=error, countdown=delay_seconds)

        marked = _record_failure(video_id, processing_token, error)
        if marked is None:
            return {
                "video_id": video_id,
                "status": "stale",
            }
        raise error


def run_branch_processing_task(task, video_id: str, branch: str, processor):
    try:
        processor(video_id)
        return {
            "video_id": video_id,
            "branch": branch,
            "status": "processed",
        }
    except TemporaryProcessingError as exc:
        failed_attempt_count = getattr(task.request, "retries", 0) + 1

        if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
            _record_branch_failure(video_id, exc, branch)
            raise

        delay_seconds = retry_delay_seconds(failed_attempt_count)
        raise task.retry(exc=exc, countdown=delay_seconds)
    except PermanentProcessingError as exc:
        _record_branch_failure(video_id, exc, branch)
        raise
    except Exception as exc:
        error = classify_processing_exception(exc)
        if isinstance(error, TemporaryProcessingError):
            failed_attempt_count = getattr(task.request, "retries", 0) + 1

            if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
                _record_branch_failure(video_id, error, branch)
                raise error

            delay_seconds = retry_delay_seconds(failed_attempt_count)
            raise task.retry(exc=error, countdown=delay_seconds)

        _record_branch_failure(video_id, error, branch)
        raise error


def run_search_orchestration_task(task, video_id: str):
    try:
        preparation = prepare_video_search_chunks(video_id)
        chunk_indexes = preparation.get("chunk_indexes", [])

        for chunk_index in chunk_indexes:
            process_video_search_chunk_task.apply_async(
                args=[video_id, chunk_index],
                queue="search",
            )

        return {
            "video_id": video_id,
            "branch": "search",
            "status": preparation.get("status", "prepared"),
            "chunk_count": preparation.get("chunk_count", len(chunk_indexes)),
            "enqueued_chunk_count": len(chunk_indexes),
        }
    except TemporaryProcessingError as exc:
        failed_attempt_count = getattr(task.request, "retries", 0) + 1

        if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
            _record_branch_failure(video_id, exc, "search")
            raise

        delay_seconds = retry_delay_seconds(failed_attempt_count)
        raise task.retry(exc=exc, countdown=delay_seconds)
    except PermanentProcessingError as exc:
        _record_branch_failure(video_id, exc, "search")
        raise
    except Exception as exc:
        error = classify_processing_exception(exc)
        if isinstance(error, TemporaryProcessingError):
            failed_attempt_count = getattr(task.request, "retries", 0) + 1

            if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
                _record_branch_failure(video_id, error, "search")
                raise error

            delay_seconds = retry_delay_seconds(failed_attempt_count)
            raise task.retry(exc=error, countdown=delay_seconds)

        _record_branch_failure(video_id, error, "search")
        raise error


def run_search_chunk_task(task, video_id: str, chunk_index: int):
    try:
        return process_video_search_chunk(
            video_id,
            chunk_index,
            processing_owner=_task_owner(task),
            max_processing_attempts=MAX_PROCESSING_ATTEMPTS,
        )
    except StaleProcessingAttempt:
        return {
            "video_id": video_id,
            "chunk_index": chunk_index,
            "status": "stale",
        }
    except TemporaryProcessingError as exc:
        failed_attempt_count = getattr(task.request, "retries", 0) + 1

        if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
            _record_chunk_failure(video_id, chunk_index, exc)
            finalize_chunked_search_if_ready(video_id)
            raise

        delay_seconds = retry_delay_seconds(failed_attempt_count)
        _record_chunk_retry(video_id, chunk_index, exc, delay_seconds)
        raise task.retry(exc=exc, countdown=delay_seconds)
    except PermanentProcessingError as exc:
        _record_chunk_failure(video_id, chunk_index, exc)
        finalize_chunked_search_if_ready(video_id)
        raise
    except Exception as exc:
        error = classify_processing_exception(exc)
        if isinstance(error, TemporaryProcessingError):
            failed_attempt_count = getattr(task.request, "retries", 0) + 1

            if attempts_exhausted(failed_attempt_count, MAX_PROCESSING_ATTEMPTS):
                _record_chunk_failure(video_id, chunk_index, error)
                finalize_chunked_search_if_ready(video_id)
                raise error

            delay_seconds = retry_delay_seconds(failed_attempt_count)
            _record_chunk_retry(video_id, chunk_index, error, delay_seconds)
            raise task.retry(exc=error, countdown=delay_seconds)

        _record_chunk_failure(video_id, chunk_index, error)
        finalize_chunked_search_if_ready(video_id)
        raise error


@celery_app.task(
    bind=True,
    name="process_video_task",
    max_retries=MAX_PROCESSING_ATTEMPTS - 1,
)
def process_video_task(self, video_id: str):
    return run_processing_task(self, video_id)


@celery_app.task(
    bind=True,
    name="process_video_search_task",
    max_retries=MAX_PROCESSING_ATTEMPTS - 1,
)
def process_video_search_task(self, video_id: str):
    return run_search_orchestration_task(self, video_id)


@celery_app.task(
    bind=True,
    name="process_video_search_chunk_task",
    max_retries=MAX_PROCESSING_ATTEMPTS - 1,
)
def process_video_search_chunk_task(self, video_id: str, chunk_index: int):
    return run_search_chunk_task(self, video_id, chunk_index)


@celery_app.task(
    bind=True,
    name="process_video_playback_task",
    max_retries=MAX_PROCESSING_ATTEMPTS - 1,
)
def process_video_playback_task(self, video_id: str):
    return run_branch_processing_task(
        self,
        video_id,
        "playback",
        process_video_playback_artifacts,
    )
