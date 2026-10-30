from datetime import datetime, timezone
import os
import logging
from uuid import UUID

from qdrant_client.models import Filter, FieldCondition, MatchValue, PointIdsList
from sqlalchemy.exc import IntegrityError

from app.db.models import OperationState, PipelineJob, UploadRequest, Video, VideoSearchChunk
from app.services.qdrant_service import COLLECTION_NAME, upsert_segments

ROOTS = ("raw_videos/", "processed_videos/", "audio_chunks/", "audio/", "transcripts/", "embeddings/", "thumbnails/", "metrics/")
GRACE_SECONDS = max(3600, int(os.getenv("ARTIFACT_GC_GRACE_SECONDS", "86400")))
logger = logging.getLogger(__name__)


def record_operation(db, name, *, error=None, details=None):
    if db.get(OperationState, name) is None:
        try:
            with db.begin_nested():
                db.add(OperationState(name=name))
                db.flush()
        except IntegrityError:
            pass
    row = db.query(OperationState).filter_by(name=name).populate_existing().with_for_update().one()
    row.updated_at, row.error = datetime.utcnow(), error
    if error is None:
        row.succeeded_at = row.updated_at
    if details is not None:
        row.details = details
    db.commit()


def object_identity(key):
    parts = key.split("/")
    if parts[0] not in {root[:-1] for root in ROOTS}:
        return None
    index = 2 if len(parts) > 2 and parts[1] in {"chunks", "final"} else 1
    if len(parts) <= index:
        return None
    candidate = parts[index].removeprefix("processed_").split("_")[0].split(".")[0]
    try:
        video_id = str(UUID(candidate))
    except ValueError:
        return None
    token = None
    if len(parts) > index + 1:
        try:
            token = str(UUID(parts[index + 1].split("_")[0].split(".")[0]))
        except ValueError:
            pass
    return video_id, token


def artifact_protected(db, video_id, token, key=None, *, now=None):
    now = now or datetime.utcnow()
    upload = db.query(UploadRequest).filter_by(video_id=video_id).with_for_update().first()
    if upload and upload.status == "uploading" and upload.expires_at and upload.expires_at > now:
        if key == upload.raw_path:
            return True
    video = db.query(Video).filter_by(video_id=video_id).populate_existing().with_for_update().first()
    if video is None:
        return False
    chunks = db.query(VideoSearchChunk).filter_by(video_id=video_id).populate_existing().all()
    if key is not None:
        fields = ("raw_path", "processed_path", "audio_path", "transcript_path", "segments_path", "embedding_path", "thumbnail_path")
        if key == f"metrics/{video_id}.json" or key == f"thumbnails/{video_id}.jpg":
            return True
        paths = [getattr(video, name) for name in fields]
        paths.extend(getattr(chunk, name) for chunk in chunks for name in ("audio_path", "transcript_path", "segments_path", "embedding_path"))
        if any(path and (path == key or (path.endswith("/") and key.startswith(path))) for path in paths):
            return True
    elif token and any(chunk.status == "completed" and chunk.artifact_token == token for chunk in chunks):
        return True
    elif token is None:
        return not chunks or any(chunk.artifact_token is None for chunk in chunks)
    for row in chunks + db.query(PipelineJob).filter_by(video_id=video_id).populate_existing().all():
        if row.status == "processing" and row.processing_token == token and row.lease_expires_at and row.lease_expires_at > now and row.deadline_at and row.deadline_at > now:
            return True
    return False


def collect_artifacts(db, storage, qdrant, *, now=None, limit=200):
    now = now or datetime.utcnow()
    cutoff = now.replace(tzinfo=timezone.utc).timestamp() - GRACE_SECONDS
    state = db.get(OperationState, "garbage_collection")
    cursors = dict((state.details or {}).get("cursors", {})) if state else {}
    db.rollback()
    removed_objects = removed_vectors = 0
    for root in ROOTS:
        objects, cursors[root] = storage.scan_objects(root, cursors.get(root), limit)
        for item in objects:
            identity = object_identity(item["Key"])
            if not identity or item["LastModified"].timestamp() > cutoff:
                continue
            protected = artifact_protected(db, *identity, item["Key"], now=now)
            db.rollback()
            if not protected:
                removed_objects += storage.delete_many([item["Key"]])
    if qdrant.collection_exists(COLLECTION_NAME):
        points, cursors["qdrant"] = qdrant.scroll(COLLECTION_NAME, limit=limit,
            offset=cursors.get("qdrant"), with_payload=True, with_vectors=False)
        for point in points:
            payload = point.payload or {}
            video_id = payload.get("video_id")
            if not video_id:
                continue
            video = db.get(Video, video_id)
            age = payload.get("written_at_epoch")
            if age is None and video and video.updated_at:
                age = video.updated_at.replace(tzinfo=timezone.utc).timestamp()
            if age is not None and age > cutoff:
                db.rollback()
                continue
            protected = artifact_protected(db, video_id, payload.get("attempt_token"), now=now)
            db.rollback()
            if not protected:
                qdrant.delete(COLLECTION_NAME, points_selector=PointIdsList(points=[point.id]), wait=True)
                removed_vectors += 1
    return {"cursors": cursors, "deleted_objects": removed_objects, "deleted_vectors": removed_vectors}


def repair_indexes(db, storage, qdrant, *, limit=10):
    state = db.get(OperationState, "index_repair")
    cursor = (state.details or {}).get("cursor", "") if state else ""
    videos = db.query(Video).filter(Video.embedding_status == "completed", Video.video_id > cursor,
        Video.status != "deleting").order_by(Video.video_id).limit(limit).all()
    ids = [video.video_id for video in videos]
    db.rollback()
    repaired = 0
    errors = []
    for video_id in ids:
        try:
            repaired += repair_video_index(db, storage, qdrant, video_id)
        except Exception as exc:
            db.rollback()
            logger.exception("Index repair failed for %s", video_id)
            errors.append({"video_id": video_id, "error": type(exc).__name__})
    return {"cursor": ids[-1] if len(ids) == limit else "", "repaired_chunks": repaired, "errors": errors}


def repair_video_index(db, storage, qdrant, video_id):
    video = db.query(Video).filter_by(video_id=video_id).populate_existing().with_for_update().first()
    if video is None or video.status == "deleting":
        db.rollback()
        return 0
    chunks = db.query(VideoSearchChunk).filter_by(video_id=video_id, status="completed").all()
    manifests = [(chunk.embedding_path, chunk.artifact_token, chunk.segment_count, chunk.chunk_index) for chunk in chunks]
    if not chunks:
        manifests = [(video.embedding_path, None, None, None)]
    db.rollback()
    repaired = 0
    for path, token, expected, index in manifests:
        conditions = [FieldCondition(key="video_id", match=MatchValue(value=video_id))]
        if token:
            conditions.append(FieldCondition(key="attempt_token", match=MatchValue(value=token)))
        elif index is not None:
            conditions.append(FieldCondition(key="chunk_index", match=MatchValue(value=index)))
        count = qdrant.count(COLLECTION_NAME, count_filter=Filter(must=conditions), exact=True).count if qdrant.collection_exists(COLLECTION_NAME) else 0
        if expected is None or count < expected:
            embeddings = storage.read_json(path)
            if embeddings:
                upsert_segments(video_id, embeddings, attempt_token=token)
                repaired += 1
    return repaired
