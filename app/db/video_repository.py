from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, func, or_

from app.db.models import Video, VideoSearchChunk


def create_video(db, video_data: dict):
    video = Video(**video_data)

    db.add(video)
    db.commit()
    db.refresh(video)

    return video


def get_video(db, video_id: str):
    return db.query(Video).filter(Video.video_id == video_id).first()


def list_videos(db, owner_user_id: str, limit: int = 25):
    return (
        db.query(Video)
        .filter(Video.owner_user_id == owner_user_id)
        .order_by(Video.created_at.desc())
        .limit(limit)
        .all()
    )


def list_public_videos(db, limit: int = 25):
    return (
        db.query(Video)
        .filter(Video.visibility == "public")
        .order_by(Video.created_at.desc())
        .limit(limit)
        .all()
    )


def update_video(db, video_id: str, updates: dict):
    video = get_video(db, video_id)

    if video is None:
        return None

    for key, value in updates.items():
        setattr(video, key, value)

    video.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(video)

    return video


def delete_search_chunks(db, video_id: str):
    deleted_count = (
        db.query(VideoSearchChunk)
        .filter(VideoSearchChunk.video_id == video_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted_count


def create_search_chunks(db, video_id: str, chunks: list[dict]):
    now = datetime.utcnow()
    rows = []

    for chunk in chunks:
        rows.append(
            VideoSearchChunk(
                video_id=video_id,
                chunk_index=chunk["chunk_index"],
                status=chunk.get("status", "pending"),
                start_seconds=chunk["start_seconds"],
                end_seconds=chunk["end_seconds"],
                audio_start_seconds=chunk["audio_start_seconds"],
                audio_end_seconds=chunk["audio_end_seconds"],
                audio_path=chunk["audio_path"],
                audio_extraction_seconds=chunk.get("audio_extraction_seconds"),
                s3_audio_upload_seconds=chunk.get("s3_audio_upload_seconds"),
                processing_attempt_count=0,
                max_processing_attempts=chunk.get("max_processing_attempts", 3),
                created_at=now,
                updated_at=now,
            )
        )

    db.add_all(rows)
    db.commit()

    return list_search_chunks(db, video_id)


def list_search_chunks(db, video_id: str):
    return (
        db.query(VideoSearchChunk)
        .filter(VideoSearchChunk.video_id == video_id)
        .order_by(VideoSearchChunk.chunk_index.asc())
        .all()
    )


def get_search_chunk(db, video_id: str, chunk_index: int):
    return (
        db.query(VideoSearchChunk)
        .filter(
            VideoSearchChunk.video_id == video_id,
            VideoSearchChunk.chunk_index == chunk_index,
        )
        .first()
    )


def list_search_chunk_artifact_keys(db, video_id: str):
    keys = []
    for chunk in list_search_chunks(db, video_id):
        keys.extend([
            chunk.audio_path,
            chunk.transcript_path,
            chunk.segments_path,
            chunk.embedding_path,
        ])
    return [key for key in keys if key]


def claim_search_chunk(
    db,
    video_id: str,
    chunk_index: int,
    processing_owner: str,
    max_processing_attempts: int,
):
    now = datetime.utcnow()

    updated_count = (
        db.query(VideoSearchChunk)
        .filter(
            VideoSearchChunk.video_id == video_id,
            VideoSearchChunk.chunk_index == chunk_index,
            or_(
                VideoSearchChunk.status == "pending",
                and_(
                    VideoSearchChunk.status == "retrying",
                    VideoSearchChunk.next_retry_at <= now,
                ),
            ),
            func.coalesce(VideoSearchChunk.processing_attempt_count, 0)
            < func.coalesce(VideoSearchChunk.max_processing_attempts, max_processing_attempts),
        )
        .update(
            {
                VideoSearchChunk.status: "processing",
                VideoSearchChunk.processing_attempt_count:
                    func.coalesce(VideoSearchChunk.processing_attempt_count, 0) + 1,
                VideoSearchChunk.max_processing_attempts:
                    func.coalesce(VideoSearchChunk.max_processing_attempts, max_processing_attempts),
                VideoSearchChunk.processing_started_at: now,
                VideoSearchChunk.processing_owner: processing_owner,
                VideoSearchChunk.next_retry_at: None,
                VideoSearchChunk.last_error_code: None,
                VideoSearchChunk.last_error_type: None,
                VideoSearchChunk.last_error_message: None,
                VideoSearchChunk.last_error_at: None,
                VideoSearchChunk.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_search_chunk(db, video_id, chunk_index)


def record_search_chunk_retry(
    db,
    video_id: str,
    chunk_index: int,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
    next_retry_at: datetime,
):
    now = datetime.utcnow()
    updated_count = (
        db.query(VideoSearchChunk)
        .filter(
            VideoSearchChunk.video_id == video_id,
            VideoSearchChunk.chunk_index == chunk_index,
            VideoSearchChunk.status == "processing",
        )
        .update(
            {
                VideoSearchChunk.status: "retrying",
                VideoSearchChunk.processing_owner: None,
                VideoSearchChunk.processing_started_at: None,
                VideoSearchChunk.last_error_code: error_code,
                VideoSearchChunk.last_error_type: error_type,
                VideoSearchChunk.last_error_message: error_message,
                VideoSearchChunk.last_error_at: now,
                VideoSearchChunk.next_retry_at: next_retry_at,
                VideoSearchChunk.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_search_chunk(db, video_id, chunk_index)


def record_search_chunk_failure(
    db,
    video_id: str,
    chunk_index: int,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
):
    now = datetime.utcnow()
    updated_count = (
        db.query(VideoSearchChunk)
        .filter(
            VideoSearchChunk.video_id == video_id,
            VideoSearchChunk.chunk_index == chunk_index,
            VideoSearchChunk.status == "processing",
        )
        .update(
            {
                VideoSearchChunk.status: "failed",
                VideoSearchChunk.processing_owner: None,
                VideoSearchChunk.processing_started_at: None,
                VideoSearchChunk.last_error_code: error_code,
                VideoSearchChunk.last_error_type: error_type,
                VideoSearchChunk.last_error_message: error_message,
                VideoSearchChunk.last_error_at: now,
                VideoSearchChunk.next_retry_at: None,
                VideoSearchChunk.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_search_chunk(db, video_id, chunk_index)


def record_search_chunk_success(
    db,
    video_id: str,
    chunk_index: int,
    updates: dict,
):
    now = datetime.utcnow()
    updates = {
        **updates,
        "status": "completed",
        "processing_owner": None,
        "next_retry_at": None,
        "completed_at": now,
        "updated_at": now,
    }

    updated_count = (
        db.query(VideoSearchChunk)
        .filter(
            VideoSearchChunk.video_id == video_id,
            VideoSearchChunk.chunk_index == chunk_index,
            VideoSearchChunk.status == "processing",
        )
        .update(updates, synchronize_session=False)
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_search_chunk(db, video_id, chunk_index)


def search_chunk_counts(db, video_id: str):
    rows = (
        db.query(VideoSearchChunk.status, func.count(VideoSearchChunk.chunk_index))
        .filter(VideoSearchChunk.video_id == video_id)
        .group_by(VideoSearchChunk.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "retrying": counts.get("retrying", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
    }


def mark_video_search_failed(
    db,
    video_id: str,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
):
    return update_video(db, video_id, {
        "status": "failed",
        "error_message": error_message,
        "transcript_status": "failed",
        "segments_status": "failed",
        "embedding_status": "failed",
        "last_error_code": error_code,
        "last_error_type": error_type,
        "last_error_message": error_message,
        "last_error_at": datetime.utcnow(),
        "next_retry_at": None,
    })


def mark_video_processed_if_ready(db, video_id: str):
    video = get_video(db, video_id)

    if video is None:
        return None

    if video.status == "processed":
        return video

    if video.embedding_status != "completed" or not video.processed_path:
        return video

    return update_video(db, video_id, {
        "status": "processed",
        "error_message": None,
        "next_retry_at": None,
    })


def record_search_processing_failure(
    db,
    video_id: str,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
):
    return update_video(db, video_id, {
        "status": "failed",
        "error_message": error_message,
        "transcript_status": "failed",
        "segments_status": "failed",
        "embedding_status": "failed",
        "last_error_code": error_code,
        "last_error_type": error_type,
        "last_error_message": error_message,
        "last_error_at": datetime.utcnow(),
        "next_retry_at": None,
    })


def record_playback_processing_failure(
    db,
    video_id: str,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
):
    return update_video(db, video_id, {
        "status": "failed",
        "error_message": error_message,
        "last_error_code": error_code,
        "last_error_type": error_type,
        "last_error_message": error_message,
        "last_error_at": datetime.utcnow(),
        "next_retry_at": None,
    })


def claim_video_for_processing(
    db,
    video_id: str,
    processing_owner: str,
    max_processing_attempts: int,
):
    processing_token = str(uuid4())
    now = datetime.utcnow()

    updated_count = (
        db.query(Video)
        .filter(
            Video.video_id == video_id,
            or_(
                Video.status == "uploaded",
                and_(
                    Video.status == "retrying",
                    Video.next_retry_at <= now,
                ),
            ),
            func.coalesce(Video.processing_attempt_count, 0)
            < func.coalesce(Video.max_processing_attempts, max_processing_attempts),
        )
        .update(
            {
                Video.status: "processing",
                Video.processing_attempt_count:
                    func.coalesce(Video.processing_attempt_count, 0) + 1,
                Video.max_processing_attempts:
                    func.coalesce(Video.max_processing_attempts, max_processing_attempts),
                Video.processing_started_at: now,
                Video.processing_owner: processing_owner,
                Video.processing_token: processing_token,
                Video.next_retry_at: None,
                Video.error_message: None,
                Video.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_video(db, video_id)


def update_video_for_processing_attempt(
    db,
    video_id: str,
    processing_token: str,
    updates: dict,
):
    updates = {
        **updates,
        "updated_at": datetime.utcnow(),
    }

    updated_count = (
        db.query(Video)
        .filter(
            Video.video_id == video_id,
            Video.processing_token == processing_token,
            Video.status == "processing",
        )
        .update(updates, synchronize_session=False)
    )
    db.commit()
    db.expire_all()

    if updated_count != 1:
        return None

    return get_video(db, video_id)


def get_video_for_processing_attempt(db, video_id: str, processing_token: str):
    return (
        db.query(Video)
        .filter(
            Video.video_id == video_id,
            Video.processing_token == processing_token,
            Video.status == "processing",
        )
        .first()
    )


def record_processing_retry(
    db,
    video_id: str,
    processing_token: str,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
    next_retry_at: datetime,
):
    return update_video_for_processing_attempt(
        db,
        video_id,
        processing_token,
        {
            "status": "retrying",
            "error_message": error_message,
            "last_error_code": error_code,
            "last_error_type": error_type,
            "last_error_message": error_message,
            "last_error_at": datetime.utcnow(),
            "next_retry_at": next_retry_at,
            "processing_started_at": None,
            "processing_owner": None,
            "processing_token": None,
        },
    )


def record_processing_failure(
    db,
    video_id: str,
    processing_token: str,
    *,
    error_code: str,
    error_type: str,
    error_message: str,
):
    return update_video_for_processing_attempt(
        db,
        video_id,
        processing_token,
        {
            "status": "failed",
            "error_message": error_message,
            "transcript_status": "failed",
            "segments_status": "failed",
            "embedding_status": "failed",
            "last_error_code": error_code,
            "last_error_type": error_type,
            "last_error_message": error_message,
            "last_error_at": datetime.utcnow(),
            "next_retry_at": None,
            "processing_started_at": None,
            "processing_owner": None,
            "processing_token": None,
        },
    )


def record_processing_success(
    db,
    video_id: str,
    processing_token: str,
    updates: dict,
):
    return update_video_for_processing_attempt(
        db,
        video_id,
        processing_token,
        {
            **updates,
            "status": "processed",
            "error_message": None,
            "next_retry_at": None,
            "processing_started_at": None,
            "processing_owner": None,
            "processing_token": None,
        },
    )


def delete_video(db, video):
    db.delete(video)
    db.commit()
