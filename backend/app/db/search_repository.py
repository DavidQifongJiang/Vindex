"""Postgres is the durable queue and publication authority for search indexes."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from uuid import uuid4

from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError

from app.db.models import (Video, SearchRevision, SearchGeneration, SearchConfiguration,
                           SearchProjection, SearchAttempt, SearchDeletion)
from app.services.search_contracts import TranscriptSnapshot
from app.services.processing_errors import StaleProcessingAttempt
from app.services.retry_policy import retry_delay_seconds

LEASE_SECONDS = 300
DEADLINE_SECONDS = 1800
GC_GRACE_SECONDS = 3600


def enabled():
    return os.getenv("SEARCH_PROJECTIONS_ENABLED", "false").lower() == "true"


def configure(db):
    """Initialize once; never silently change an active generation's schema/model."""
    lexical = os.getenv("SEARCH_LEXICAL_TARGET", "vindex-library-passages-v1")
    specs = [("lexical-v1", "lexical", lexical, {"schema": 1})]
    model_revision = os.getenv("SEARCH_EMBEDDING_REVISION")
    if model_revision:
        specs.append(("semantic-v1", "semantic", os.getenv("SEARCH_SEMANTIC_TARGET", "vindex_library_v1"),
            {"schema": 1, "model": os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
             "model_revision": model_revision, "dimension": int(os.getenv("SEARCH_VECTOR_SIZE", "384"))}))
    for identity, backend, target, settings in specs:
        row = db.get(SearchGeneration, identity)
        if row is None:
            try:
                with db.begin_nested():
                    db.add(SearchGeneration(generation_id=identity, backend=backend, target=target, settings=settings))
                    db.flush()
            except IntegrityError:
                pass
            row = db.get(SearchGeneration, identity)
        if row is None or (row.backend, row.target, row.settings) != (backend, target, settings):
            raise ValueError("Search generation configuration changed; create a new reviewed generation")
    if db.get(SearchConfiguration, "active") is None:
        try:
            with db.begin_nested():
                db.add(SearchConfiguration(name="active", generations={backend: identity for identity, backend, _, _ in specs}))
                db.flush()
        except IntegrityError:
            pass
    return db.get(SearchConfiguration, "active").generations


def register_revision(db, video_id, path, manifest_bytes, embedding_path=None):
    """Caller transaction atomically accepts the source and creates task intent.

    The video must already point at this accepted manifest (possibly uncommitted).
    No I/O here: content was fetched/written before taking the short parent lock.
    """
    video = db.query(Video).filter_by(video_id=video_id).populate_existing().with_for_update().one()
    if (video.status == "deleting" or db.get(SearchDeletion, video_id) is not None
            or video.segments_status != "completed" or video.segments_path != path):
        raise StaleProcessingAttempt("Source is no longer accepted")
    generations = configure(db)
    key = sha256(json.dumps([video_id, path]).encode()).hexdigest()
    revision = db.query(SearchRevision).filter_by(source_key=key).one_or_none()
    revision_id = revision.revision_id if revision else str(uuid4())
    snapshot = TranscriptSnapshot.from_manifest(video_id, revision_id, manifest_bytes,
        expected_sha256=revision.manifest_sha256 if revision else None)
    if revision is None:
        revision = SearchRevision(revision_id=revision_id, video_id=video_id, source_key=key,
            segments_path=path, manifest_sha256=snapshot.manifest_sha256, passage_count=len(snapshot.passages),
            embedding_path=embedding_path)
        db.add(revision)
        db.flush()
    video.current_search_revision_id = revision_id
    db.query(SearchProjection).filter(SearchProjection.video_id == video_id,
        SearchProjection.revision_id != revision_id).update({"status": "obsolete", "accepted_attempt_id": None}, synchronize_session=False)
    for generation in generations.values():
        if db.query(SearchProjection).filter_by(video_id=video_id, revision_id=revision_id, generation_id=generation).first() is None:
            db.add(SearchProjection(job_id=str(uuid4()), video_id=video_id, revision_id=revision_id,
                                    generation_id=generation, status="pending", attempt_count=0, max_attempts=3))
    db.flush()
    return revision


def lock_job(db, job_id):
    # Uniform ordering with deletion/publication: parent first, then projection.
    identity = db.query(SearchProjection.video_id).filter_by(job_id=job_id).scalar()
    if identity is None:
        return None
    video = db.query(Video).filter_by(video_id=identity).populate_existing().with_for_update().first()
    row = db.query(SearchProjection).filter_by(job_id=job_id).populate_existing().with_for_update().one()
    revision = db.get(SearchRevision, row.revision_id)
    if (video is None or video.status == "deleting" or db.get(SearchDeletion, identity)
            or video.current_search_revision_id != row.revision_id or video.segments_path != revision.segments_path):
        row.status, row.accepted_attempt_id = "obsolete", None
        return None
    return row


def claim(db, job_id, *, now=None):
    now = now or datetime.utcnow()
    row = lock_job(db, job_id)
    if (row is None or row.status not in {"pending", "retrying"}
            or row.attempt_count >= row.max_attempts or (row.next_retry_at and row.next_retry_at > now)):
        db.commit()
        return None
    token = str(uuid4())
    row.status, row.token = "indexing", token
    row.attempt_count += 1
    row.lease_expires_at, row.deadline_at = now + timedelta(seconds=LEASE_SECONDS), now + timedelta(seconds=DEADLINE_SECONDS)
    row.next_retry_at, row.error_code = None, None
    db.add(SearchAttempt(attempt_id=token, job_id=job_id, deadline_at=row.deadline_at,
                         cleanup_after=row.deadline_at + timedelta(seconds=GC_GRACE_SECONDS)))
    db.commit()
    return token


def guard(db, job_id, token, *, now=None):
    now = now or datetime.utcnow()
    row = lock_job(db, job_id)
    if (row is None or row.status != "indexing" or row.token != token
            or row.lease_expires_at <= now or row.deadline_at <= now):
        db.commit()
        raise StaleProcessingAttempt("Indexing attempt no longer owns publication")
    return row


def renew(db, job_id, token):
    now = datetime.utcnow()
    row = guard(db, job_id, token, now=now)
    row.lease_expires_at = min(row.deadline_at, now + timedelta(seconds=LEASE_SECONDS))
    db.commit()


def publish(db, job_id, token):
    row = guard(db, job_id, token)
    row.status, row.accepted_attempt_id = "ready", token
    row.token = row.lease_expires_at = row.error_code = None
    row.verified_at = datetime.utcnow()
    db.commit()


def fail(db, job_id, token, error_code, *, retryable, now=None, expired=False):
    now = now or datetime.utcnow()
    row = lock_job(db, job_id)
    if row is None or row.status != "indexing" or row.token != token:
        db.commit()
        return False
    if expired and row.lease_expires_at > now and row.deadline_at > now:
        db.rollback()
        return False
    if not expired and (row.lease_expires_at <= now or row.deadline_at <= now):
        db.rollback()
        return False
    terminal = not retryable or row.attempt_count >= row.max_attempts
    row.status = "failed" if terminal else "retrying"
    row.error_code = error_code[:200]
    row.next_retry_at = None if terminal else now + timedelta(seconds=retry_delay_seconds(row.attempt_count))
    row.token = row.lease_expires_at = row.dispatch_after = None
    db.commit()
    return True


def dispatch(db, send, *, limit=100, now=None):
    now = now or datetime.utcnow()
    expired_ids = db.query(SearchProjection.job_id, SearchProjection.token).filter(SearchProjection.status == "indexing",
        or_(SearchProjection.lease_expires_at <= now, SearchProjection.deadline_at <= now)).limit(limit).all()
    db.rollback()
    for identity, token in expired_ids:
        fail(db, identity, token, "index_lease_expired", retryable=True, expired=True, now=now)
    ids = db.query(SearchProjection.job_id).filter(or_(SearchProjection.status == "pending",
        and_(SearchProjection.status == "retrying", SearchProjection.next_retry_at <= now)),
        or_(SearchProjection.dispatch_after.is_(None), SearchProjection.dispatch_after <= now)
        ).order_by(SearchProjection.created_at).limit(limit).all()
    db.rollback()
    sent = 0
    for (identity,) in ids:
        row = lock_job(db, identity)
        if (row is None or row.status not in {"pending", "retrying"}
                or (row.next_retry_at and row.next_retry_at > now)
                or (row.dispatch_after and row.dispatch_after > now)):
            db.commit()
            continue
        row.dispatch_after = now + timedelta(seconds=60)
        db.commit()
        # Ambiguous publication leaves durable intent for redispatch.
        send("index_search_projection", args=[identity], queue="indexing")
        sent += 1
    return sent


def revoke_video(db, video_id):
    """Caller holds video lock; commit with the deleting transition."""
    if db.get(SearchDeletion, video_id) is None:
        db.add(SearchDeletion(video_id=video_id))
    db.query(SearchProjection).filter_by(video_id=video_id).update(
        {"status": "obsolete", "accepted_attempt_id": None}, synchronize_session=False)


def request_repair(db, job_id, *, expected_attempt=None):
    row = lock_job(db, job_id)
    if row is None:
        db.commit()
        return False
    if expected_attempt is not None and (row.status != "ready" or row.accepted_attempt_id != expected_attempt):
        db.rollback()
        return False
    if row.status not in {"ready", "failed"}:
        db.rollback()
        return False
    row.status, row.attempt_count = "pending", 0
    row.accepted_attempt_id = row.token = row.next_retry_at = row.dispatch_after = None
    row.error_code = "repair_requested"
    db.commit()
    return True


def published_lexical(db, video):
    config = db.get(SearchConfiguration, "active")
    generation_id = config.generations.get("lexical") if config else None
    if not generation_id or not video.current_search_revision_id or video.status == "deleting":
        return None
    row = db.query(SearchProjection).filter_by(video_id=video.video_id,
        revision_id=video.current_search_revision_id, generation_id=generation_id, status="ready").first()
    if row is None or not row.accepted_attempt_id:
        return None
    revision = db.get(SearchRevision, row.revision_id)
    if revision.segments_path != video.segments_path:
        return None
    generation = db.get(SearchGeneration, generation_id)
    return {"job_id": row.job_id, "attempt_id": row.accepted_attempt_id, "target": generation.target}
