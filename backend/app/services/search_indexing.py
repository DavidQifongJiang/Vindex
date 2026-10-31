"""Projection execution, bounded backfill, integrity repair and orphan collection."""
from datetime import datetime, timedelta
import json
import logging
from threading import Event, Thread

from sqlalchemy import or_

from app.db import search_repository as repo
from app.db.models import Video, SearchRevision, SearchProjection, SearchAttempt, SearchGeneration, OperationState
from app.db.session import create_session
from app.services.search_contracts import TranscriptSnapshot
from app.services.processing_errors import StaleProcessingAttempt, classify_processing_exception
from app.services.projection_indexes import ElasticsearchProjectionIndex, QdrantProjectionIndex, aligned_embeddings

logger = logging.getLogger(__name__)


def index_for(backend, target, settings):
    if backend == "lexical":
        return ElasticsearchProjectionIndex(target)
    if backend == "semantic":
        from app.services.qdrant_service import get_qdrant_client
        return QdrantProjectionIndex(target, settings["dimension"], get_qdrant_client())
    raise ValueError("Unknown projection backend")


def specification(db, job_id):
    job = db.get(SearchProjection, job_id)
    revision = db.get(SearchRevision, job.revision_id)
    generation = db.get(SearchGeneration, job.generation_id)
    return {"video_id": job.video_id, "revision_id": job.revision_id,
        "segments_path": revision.segments_path, "checksum": revision.manifest_sha256,
        "count": revision.passage_count, "embedding_path": revision.embedding_path,
        "backend": generation.backend, "target": generation.target, "settings": dict(generation.settings)}


def load_source(storage, spec):
    raw = storage.read_bytes(spec["segments_path"])
    snapshot = TranscriptSnapshot.from_manifest(spec["video_id"], spec["revision_id"], raw, expected_sha256=spec["checksum"])
    if len(snapshot.passages) != spec["count"]:
        raise ValueError("Accepted passage count changed")
    vectors = None
    if spec["backend"] == "semantic":
        vectors = aligned_embeddings(json.loads(raw), storage.read_json(spec["embedding_path"]), spec["settings"])
    return snapshot, vectors


class ProjectionLease:
    def __init__(self, job_id, token, sessions):
        self.job_id, self.token, self.sessions = job_id, token, sessions
        self.stop, self.lost = Event(), Event()
        self.thread = Thread(target=self.heartbeat, daemon=True)

    def check(self):
        if self.lost.is_set():
            raise StaleProcessingAttempt("Indexing lease lost")
        with self.sessions() as db:
            repo.renew(db, self.job_id, self.token)

    def heartbeat(self):
        while not self.stop.wait(repo.LEASE_SECONDS / 3):
            try:
                self.check()
            except StaleProcessingAttempt:
                self.lost.set()
                return
            except Exception:
                logger.exception("Search projection heartbeat failed", extra={"job_id": self.job_id})

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)


def process_projection(job_id, storage, *, sessions=create_session, indexes=index_for):
    with sessions() as db:
        token = repo.claim(db, job_id)
        if token is None:
            return {"status": "skipped"}
        spec = specification(db, job_id)
    index = None
    try:
        with ProjectionLease(job_id, token, sessions) as lease:
            snapshot, vectors = load_source(storage, spec)
            lease.check()
            index = indexes(spec["backend"], spec["target"], spec["settings"])
            index.write(snapshot, token, vectors, checkpoint=lease.check)
            if not index.verify(snapshot, token, vectors, checkpoint=lease.check):
                raise ValueError("Projection integrity verification failed")
            with sessions() as db:
                repo.publish(db, job_id, token)
        return {"status": "ready", "projection_id": token}
    except StaleProcessingAttempt:
        return {"status": "stale"}
    except Exception as exc:
        error = classify_processing_exception(exc, default_error_code="search_index_failed")
        # Bad source/mapping/model must be repaired explicitly, never retried forever.
        retryable = False if isinstance(exc, (ValueError, KeyError, TypeError)) else error.retryable
        with sessions() as db:
            marked = repo.fail(db, job_id, token, error.error_code, retryable=retryable)
        logger.exception("Search projection failed", extra={"job_id": job_id, "attempt_id": token})
        return {"status": "failed_or_retrying" if marked else "stale", "error_code": error.error_code}
    finally:
        if index:
            index.close()


def reconcile(db, storage, *, indexes=index_for, limit=10, now=None):
    now = now or datetime.utcnow()
    state = db.get(OperationState, "search_projections")
    cursor = (state.details or {}).get("cursor", "") if state else ""
    rows = db.query(Video.video_id, Video.segments_path, Video.embedding_path).filter(Video.video_id > cursor,
        Video.status != "deleting", Video.segments_status == "completed", Video.segments_path.isnot(None)
        ).order_by(Video.video_id).limit(limit).all()
    db.rollback()
    errors, registered, repaired, cleaned = [], 0, 0, 0
    for video_id, path, embedding_path in rows:
        try:
            raw = storage.read_bytes(path)
            repo.register_revision(db, video_id, path, raw, embedding_path)
            db.commit()
            registered += 1
        except Exception as exc:
            db.rollback()
            errors.append({"video_id": video_id, "operation": "register", "error": type(exc).__name__})
    ready = db.query(SearchProjection.job_id, SearchProjection.accepted_attempt_id).filter_by(status="ready").order_by(
        SearchProjection.verified_at.asc().nullsfirst(), SearchProjection.job_id).limit(limit).all()
    db.rollback()
    for job_id, attempt in ready:
        index = None
        try:
            spec = specification(db, job_id)
            db.rollback()
            snapshot, vectors = load_source(storage, spec)
            index = indexes(spec["backend"], spec["target"], spec["settings"])
            healthy = index.verify(snapshot, attempt, vectors)
            if not healthy:
                repaired += repo.request_repair(db, job_id, expected_attempt=attempt)
            else:
                row = repo.lock_job(db, job_id)
                if row and row.status == "ready" and row.accepted_attempt_id == attempt:
                    row.verified_at = now
                db.commit()
        except Exception as exc:
            db.rollback()
            # Advance the verification cursor even on outage to avoid starving
            # other jobs. Preserve eligibility on uncertain network failures.
            row = repo.lock_job(db, job_id)
            if row and row.status == "ready" and row.accepted_attempt_id == attempt:
                row.verified_at = now
                if isinstance(exc, (ValueError, KeyError, TypeError)):
                    row.status, row.accepted_attempt_id, row.error_code = "failed", None, "source_integrity_failed"
            db.commit()
            errors.append({"job_id": job_id, "operation": "verify", "error": type(exc).__name__})
        finally:
            if index:
                index.close()
    attempts = db.query(SearchAttempt.attempt_id, SearchAttempt.job_id).filter(SearchAttempt.cleanup_after <= now,
        or_(SearchAttempt.cleaned_at.is_(None), SearchAttempt.cleaned_at <= now - timedelta(seconds=3600))
        ).order_by(SearchAttempt.cleaned_at.asc().nullsfirst(), SearchAttempt.attempt_id).limit(limit).all()
    db.rollback()
    for attempt_id, job_id in attempts:
        index = None
        try:
            row = repo.lock_job(db, job_id)
            protected = row and (row.accepted_attempt_id == attempt_id or (row.status == "indexing" and row.token == attempt_id))
            spec = specification(db, job_id)
            # Commit obsolete status even when the video row is gone.
            db.commit()
            if not protected:
                index = indexes(spec["backend"], spec["target"], spec["settings"])
                index.delete(attempt_id)
                cleaned += 1
            attempt = db.get(SearchAttempt, attempt_id)
            attempt.cleaned_at, attempt.error_code = now, None
            db.commit()
        except Exception as exc:
            db.rollback()
            attempt = db.get(SearchAttempt, attempt_id)
            attempt.cleaned_at, attempt.error_code = now, type(exc).__name__
            db.commit()
            errors.append({"attempt_id": attempt_id, "operation": "cleanup", "error": type(exc).__name__})
        finally:
            if index:
                index.close()
    return {"cursor": rows[-1][0] if len(rows) == limit else "", "registered": registered,
            "repaired": repaired, "cleaned_attempts": cleaned, "errors": errors}
