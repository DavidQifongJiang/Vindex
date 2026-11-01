from datetime import datetime, timedelta
import hashlib
import json
import logging
import os
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.db.models import UploadRequest, Video
from app.db.video_repository import create_video

logger = logging.getLogger(__name__)


def upload_fingerprint(fileobj, *, filename, title, visibility, max_bytes):
    digest = hashlib.sha256()
    size = 0
    fileobj.seek(0)
    while chunk := fileobj.read(1024 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, "Uploaded video is too large")
        digest.update(chunk)
    fileobj.seek(0)
    metadata = json.dumps([filename, title, visibility, size, digest.hexdigest()], separators=(",", ":"))
    return hashlib.sha256(metadata.encode()).hexdigest()


def upload_intent_fingerprint(*, filename, title, visibility, size_bytes, content_type):
    """Fingerprint a direct-to-S3 upload from what the client declares.

    The API never sees the bytes in this flow, so the declared size is part of
    the identity and S3 enforces it exactly through the presigned POST policy.
    """
    metadata = json.dumps(["direct", filename, title, visibility, int(size_bytes), content_type],
                          separators=(",", ":"))
    return hashlib.sha256(metadata.encode()).hexdigest()


def validate_idempotency_key(key):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key or ""):
        raise HTTPException(400, "Invalid Idempotency-Key")


def reserve_upload(db, owner, key, fingerprint, suffix, *, resume_inflight=False, pending_response=None):
    """Reserve an upload attempt for (owner, key).

    With ``resume_inflight`` an unexpired attempt for the same content is
    returned unchanged, so a client that lost the presigned URL can ask again
    and keep uploading to the same attempt-specific key.
    """
    validate_idempotency_key(key)
    identity = (owner, key)
    if db.get(UploadRequest, identity) is None:
        try:
            with db.begin_nested():
                db.add(UploadRequest(owner_user_id=owner, idempotency_key=key,
                    fingerprint=fingerprint, video_id=str(uuid4()), status="pending"))
                db.flush()
        except IntegrityError:
            pass
    row = db.query(UploadRequest).filter_by(owner_user_id=owner, idempotency_key=key).populate_existing().with_for_update().one()
    now = datetime.utcnow()
    if row.fingerprint != fingerprint:
        db.rollback()
        raise HTTPException(409, "Idempotency-Key was already used for different upload content")
    if row.status == "completed":
        video = db.get(Video, row.video_id)
        if video is None or video.status == "deleting":
            db.rollback()
            raise HTTPException(410, "This upload was deleted; use a new Idempotency-Key")
        response = dict(row.response)
        db.commit()
        return None, response
    if row.expires_at and row.expires_at > now:
        if resume_inflight and row.status == "uploading":
            db.commit()
            db.refresh(row)
            return row, None
        db.rollback()
        raise HTTPException(409, "This upload is already in progress; retry with the same key", headers={"Retry-After": "5"})
    row.processing_token = str(uuid4())
    row.raw_path = f"raw_videos/{row.video_id}/{row.processing_token}/{row.video_id}{suffix}"
    row.expires_at = now + timedelta(seconds=int(os.getenv("UPLOAD_DEADLINE_SECONDS", "1800")))
    row.status = "uploading"
    row.updated_at = now
    if pending_response is not None:
        row.response = dict(pending_response, video_id=row.video_id)
    db.commit()
    db.refresh(row)
    return row, None


def find_direct_upload(db, owner, key, video_id):
    """Return ``(row, replay)`` for completing a direct upload.

    ``replay`` is the stored response when the upload already completed;
    otherwise ``row`` is the live, unexpired reservation (detached copy).
    """
    validate_idempotency_key(key)
    row = db.query(UploadRequest).filter_by(owner_user_id=owner, idempotency_key=key).populate_existing().first()
    if row is None or row.video_id != video_id:
        db.rollback()
        raise HTTPException(404, "Upload not found")
    if row.status == "completed":
        video = db.get(Video, row.video_id)
        db.rollback()
        if video is None or video.status == "deleting":
            raise HTTPException(410, "This upload was deleted; use a new Idempotency-Key")
        return None, dict(row.response)
    if row.status != "uploading" or not row.expires_at or row.expires_at <= datetime.utcnow():
        db.rollback()
        raise HTTPException(409, "Upload attempt expired; request a new upload URL with the same key")
    db.expunge(row)
    db.rollback()
    return row, None


def finish_upload(db, owner, key, token, response):
    row = db.query(UploadRequest).filter_by(owner_user_id=owner, idempotency_key=key).populate_existing().with_for_update().one()
    if row.processing_token != token or not row.expires_at or row.expires_at <= datetime.utcnow():
        db.rollback()
        raise HTTPException(409, "Upload attempt expired; retry with the same key")
    row.response, row.status = response, "completed"
    row.expires_at = row.processing_token = None
    row.updated_at = datetime.utcnow()
    db.flush()


def release_upload(db, owner, key, token):
    db.rollback()
    db.query(UploadRequest).filter_by(owner_user_id=owner, idempotency_key=key,
        processing_token=token, status="uploading").update({"expires_at": datetime.utcnow()})
    db.commit()


def abandon_upload(db, owner, key, token):
    try:
        release_upload(db, owner, key, token)
    except Exception:
        db.rollback()
        logger.exception("Could not release upload reservation; expiry will recover it")


def commit_upload(db, owner, key, token, response, metadata):
    try:
        finish_upload(db, owner, key, token, response)
        return create_video(db, metadata, enqueue_processing=True)
    except Exception:
        abandon_upload(db, owner, key, token)
        raise
