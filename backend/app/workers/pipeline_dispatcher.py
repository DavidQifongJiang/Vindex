"""Recover expired leases and dispatch the Postgres-backed outbox independently of Redis."""
from datetime import datetime, timedelta
import logging
import os
from threading import Event
from uuid import uuid4

from sqlalchemy import and_, or_

from app.db.models import PipelineJob, VideoSearchChunk
from app.db.pipeline_repository import REDISPATCH_SECONDS, fail_work, lock_video, work_query
from app.db.session import create_session
from app.services.processing_errors import StaleProcessingAttempt, TemporaryProcessingError
from app.services.maintenance_service import record_operation

logger = logging.getLogger(__name__)
TASKS = {
    "search": ("process_video_search_task", "search"),
    "playback": ("process_video_playback_task", "playback"),
    "finalize": ("finalize_video_search_task", "search"),
}


def due_work(model, now):
    return or_(model.status == "pending", and_(model.status == "retrying", model.next_retry_at <= now))


def recover_expired(db, *, now=None, limit=100):
    now = now or datetime.utcnow()
    recovered = 0
    for model in (PipelineJob, VideoSearchChunk):
        candidates = db.query(model).filter(or_(
            and_(model.status == "processing", or_(model.lease_expires_at <= now, model.deadline_at <= now)),
            and_(model.status.in_(["pending", "retrying"]),
                 model.processing_attempt_count >= model.max_processing_attempts),
        )).limit(limit).all()
        keys = [(row.video_id, row.chunk_index if model is VideoSearchChunk else row.stage) for row in candidates]
        db.rollback()
        for video_id, key in keys:
            try:
                lock_video(db, video_id)
                row = work_query(db, model, video_id, key).populate_existing().first()
                if row is None:
                    db.rollback()
                    continue
                if row.status in {"pending", "retrying"} and row.processing_attempt_count >= row.max_processing_attempts:
                    row.status = "processing"
                    row.processing_token = str(uuid4())
                    row.lease_expires_at = now
                    db.flush()
                deadline_expired = row.deadline_at is not None and row.deadline_at <= now
                if row.status != "processing" or (not deadline_expired and (row.lease_expires_at is None or row.lease_expires_at > now)):
                    db.rollback()
                    continue
                result = fail_work(db, model, video_id, key, row.processing_token,
                    TemporaryProcessingError("Processing deadline exceeded" if deadline_expired else "Worker lease expired",
                        error_code="processing_deadline_exceeded" if deadline_expired else "worker_lease_expired"),
                    now=now, allow_expired=True)
                recovered += result is not None
            except StaleProcessingAttempt:
                db.rollback()
    return recovered


def dispatch_pending(db, publish, *, now=None, limit=100):
    now = now or datetime.utcnow()
    dispatched = 0
    for model in (PipelineJob, VideoSearchChunk):
        candidates = db.query(model).filter(
            due_work(model, now),
            model.processing_attempt_count < model.max_processing_attempts,
            or_(model.dispatch_after.is_(None), model.dispatch_after <= now),
        ).order_by(model.created_at).limit(limit).all()
        keys = [(row.video_id, row.chunk_index if model is VideoSearchChunk else row.stage) for row in candidates]
        db.rollback()
        for video_id, key in keys:
            try:
                lock_video(db, video_id)
            except StaleProcessingAttempt:
                db.rollback()
                continue
            count = work_query(db, model, video_id, key).filter(
                due_work(model, now), or_(model.dispatch_after.is_(None), model.dispatch_after <= now),
            ).update({"dispatch_after": now + timedelta(seconds=REDISPATCH_SECONDS)}, synchronize_session=False)
            db.commit()
            if not count:
                continue
            task, queue = ("process_video_search_chunk_task", "search") if model is VideoSearchChunk else TASKS[key]
            args = [video_id, key] if model is VideoSearchChunk else [video_id]
            # A crash or ambiguous broker response is safe: unclaimed work is published again.
            try:
                publish(task, args=args, queue=queue)
                dispatched += 1
            except Exception:
                logger.exception("Could not publish %s for %s; durable work retained", task, video_id)
                # Avoid spending a full batch on a failed broker before recovering leases again.
                return dispatched
    return dispatched


def main():
    from app.workers.celery_app import celery_app

    logging.basicConfig(level=logging.INFO)
    stop = Event()
    interval = max(1, int(os.getenv("PIPELINE_DISPATCH_INTERVAL_SECONDS", "5")))
    while not stop.is_set():
        db = create_session()
        try:
            recover_expired(db)
            dispatch_pending(db, lambda name, **kwargs: celery_app.send_task(name, retry=False, **kwargs))
            from app.db import search_repository
            if search_repository.enabled():
                search_repository.dispatch(db, lambda name, **kwargs: celery_app.send_task(name, retry=False, **kwargs))
            record_operation(db, "dispatcher")
        except Exception:
            logger.exception("Pipeline dispatcher iteration failed")
        finally:
            db.close()
        stop.wait(interval)


if __name__ == "__main__":
    main()
