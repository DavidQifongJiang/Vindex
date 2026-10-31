from datetime import datetime, timedelta
import os
from uuid import uuid4

from sqlalchemy import and_, or_, func

from app.db.models import PipelineJob, Video, VideoSearchChunk
from app.services.processing_errors import StaleProcessingAttempt
from app.services.retry_policy import MAX_PROCESSING_ATTEMPTS, retry_delay_seconds

LEASE_SECONDS = max(30, int(os.getenv("PROCESSING_LEASE_SECONDS", "300")))
REDISPATCH_SECONDS = max(10, int(os.getenv("PIPELINE_REDISPATCH_SECONDS", "60")))
TASK_DEADLINE_SECONDS = max(1, int(os.getenv("PROCESSING_DEADLINE_SECONDS", "1800")))


def work_query(db, model, video_id, key):
    column = model.chunk_index if model is VideoSearchChunk else model.stage
    return db.query(model).filter(model.video_id == video_id, column == key)


def lock_video(db, video_id):
    video = db.query(Video).filter(Video.video_id == video_id).populate_existing().with_for_update().first()
    if video is None or video.status == "deleting":
        raise StaleProcessingAttempt("Video is no longer available for processing")
    return video


def add_job(db, video_id, stage):
    """Caller holds the parent video lock; insert in the caller's transaction."""
    job = work_query(db, PipelineJob, video_id, stage).first()
    if job is None:
        job = PipelineJob(video_id=video_id, stage=stage, status="pending",
                          max_processing_attempts=MAX_PROCESSING_ATTEMPTS)
        db.add(job)
        db.flush()
    return job


def schedule_finalization(db, video_id):
    chunks = db.query(VideoSearchChunk).filter_by(video_id=video_id).all()
    if chunks and all(chunk.status == "completed" for chunk in chunks):
        add_job(db, video_id, "finalize")


def claim_work(db, model, video_id, key, owner, max_attempts=MAX_PROCESSING_ATTEMPTS):
    try:
        lock_video(db, video_id)
    except StaleProcessingAttempt:
        db.rollback()
        return None
    now = datetime.utcnow()
    token = str(uuid4())
    count = work_query(db, model, video_id, key).filter(
        or_(model.status == "pending", and_(model.status == "retrying", model.next_retry_at <= now)),
        func.coalesce(model.processing_attempt_count, 0) < func.coalesce(model.max_processing_attempts, max_attempts),
    ).update({
        "status": "processing", "processing_token": token, "processing_owner": owner,
        "processing_attempt_count": func.coalesce(model.processing_attempt_count, 0) + 1,
        "processing_started_at": now, "lease_expires_at": now + timedelta(seconds=LEASE_SECONDS),
        "deadline_at": now + timedelta(seconds=TASK_DEADLINE_SECONDS), "progress_at": now,
        "next_retry_at": None, "updated_at": now,
    }, synchronize_session=False)
    db.commit()
    db.expire_all()
    return work_query(db, model, video_id, key).first() if count else None


def guard_work(db, model, video_id, key, token, *, now=None):
    """Lock parent and renew ownership in the transaction that publishes outputs."""
    lock_video(db, video_id)
    now = now or datetime.utcnow()
    count = work_query(db, model, video_id, key).filter(
        model.status == "processing", model.processing_token == token,
        model.lease_expires_at > now,
        model.deadline_at > now,
    ).update({"lease_expires_at": now + timedelta(seconds=LEASE_SECONDS)}, synchronize_session=False)
    if count != 1:
        db.rollback()
        raise StaleProcessingAttempt("Processing attempt no longer owns this work")


def renew_work(db, model, video_id, key, token, *, progress=False):
    guard_work(db, model, video_id, key, token)
    if progress:
        work_query(db, model, video_id, key).update({"progress_at": datetime.utcnow()}, synchronize_session=False)
    db.commit()


def complete_work(db, model, video_id, key, token, updates=None):
    guard_work(db, model, video_id, key, token)
    values = {
        **(updates or {}), "status": "completed", "processing_owner": None,
        "processing_token": None, "lease_expires_at": None, "next_retry_at": None,
        "completed_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
        "last_error_code": None, "last_error_type": None, "last_error_message": None,
        "last_error_at": None,
    }
    if model is VideoSearchChunk:
        values["artifact_token"] = token
    work_query(db, model, video_id, key).update(values, synchronize_session=False)
    db.flush()
    db.expire_all()
    if model is VideoSearchChunk or (model is PipelineJob and key == "search"):
        schedule_finalization(db, video_id)
    if model is PipelineJob and key in {"playback", "finalize"}:
        video = db.get(Video, video_id)
        if video.embedding_status == "completed" and video.processed_path:
            video.status = "processed"
            video.error_message = None
            video.updated_at = datetime.utcnow()
    db.commit()
    return work_query(db, model, video_id, key).first()


def fail_work(db, model, video_id, key, token, error, *, now=None, allow_expired=False):
    now = now or datetime.utcnow()
    video = lock_video(db, video_id)
    query = work_query(db, model, video_id, key).filter(
        model.status == "processing", model.processing_token == token)
    if allow_expired:
        query = query.filter(or_(model.lease_expires_at <= now, model.deadline_at <= now))
    else:
        query = query.filter(model.lease_expires_at > now)
    row = query.populate_existing().first()
    if row is None:
        db.rollback()
        return None
    terminal = not error.retryable or row.processing_attempt_count >= row.max_processing_attempts
    row.status = "failed" if terminal else "retrying"
    row.next_retry_at = None if terminal else now + timedelta(seconds=retry_delay_seconds(row.processing_attempt_count))
    row.dispatch_after = None
    row.processing_owner = row.processing_token = row.lease_expires_at = None
    row.last_error_code = error.error_code
    row.last_error_type = type(error).__name__
    row.last_error_message = str(error)[:1000]
    row.last_error_at = row.updated_at = now
    if terminal:
        video.status = "failed"
        video.error_message = video.last_error_message = row.last_error_message
        video.last_error_code = row.last_error_code
        video.last_error_type = row.last_error_type
        video.last_error_at = now
        if model is VideoSearchChunk or key in {"search", "finalize"}:
            video.transcript_status = video.segments_status = video.embedding_status = "failed"
        video.updated_at = now
    # The retrying row is itself the durable outbox entry; no broker call occurs here.
    db.commit()
    return row


def has_active_work(db, video_id):
    return any(db.query(model).filter(model.video_id == video_id,
        model.status.in_(["pending", "processing", "retrying"])).first() is not None
        for model in (PipelineJob, VideoSearchChunk))
