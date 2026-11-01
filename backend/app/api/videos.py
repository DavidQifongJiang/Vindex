
from datetime import datetime
import os
from pathlib import Path
import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from pydantic import BaseModel
from app.services.search_service import score_segment
from app.services.hybrid_search import BM25, CANDIDATES, reciprocal_rank_fusion
from app.services.elasticsearch_service import IndexNotReady
from app.services.lexical_index import LexicalIndex as ElasticsearchSearch
import requests
from app.db.models import PipelineJob
from app.db.pipeline_repository import has_active_work
from app.db.upload_repository import (
    abandon_upload, commit_upload, find_direct_upload, reserve_upload, upload_fingerprint,
    upload_intent_fingerprint,
)
from app.api.schemas import (
    CreateUploadRequest, CreateUploadResponse, UploadAccepted, VideoListResponse,
    VideoStatusResponse, video_summary,
)

from app.db.session import get_db
from app.db.video_repository import (
    create_video,
    delete_search_chunks,
    delete_video,
    get_video,
    list_public_videos,
    list_search_chunk_artifact_keys,
    list_search_chunks,
    list_videos,
)
from app.db.user_repository import upsert_user
from sqlalchemy.orm import Session

from app.services.qdrant_service import delete_segments, search_segments
from app.services.search_service import encode_text
from app.services.auth_service import AuthUser, get_current_user, get_optional_user


from app.services.storage_service import S3Storage


from time import perf_counter
from app.services.metrics_service import metrics_key, now_epoch, seconds_since, safe_update_metrics, read_metrics


storage = S3Storage()

router = APIRouter()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
VALID_VISIBILITIES = {"private", "public"}
ACTIVE_DELETE_BLOCK_STATUSES = {"uploaded", "processing", "retrying"}


class SearchRequest(BaseModel):
    query: str
    algorithm: Literal[
    "embedding",
    "qdrant_embedding",
    "bm25",
    "hybrid",
    "elasticsearch_bm25",
    "elasticsearch_hybrid",
    "exact",
    "overlap",
    "stopword_overlap"
] = "qdrant_embedding"


@router.get("/videos", response_model=VideoListResponse)
def get_videos(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    upsert_user(db, current_user)

    return {
        "videos": [video_summary(video, current_user) for video in list_videos(db, current_user.user_id)]
    }


@router.get("/public/videos", response_model=VideoListResponse)
def get_public_videos(
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    return {
        "videos": [video_summary(video, current_user) for video in list_public_videos(db)]
    }


def normalize_title(title: str | None, filename: str | None):
    candidate = (title or "").strip()
    if not candidate:
        candidate = Path(filename or "Untitled video").stem

    return candidate[:120] or "Untitled video"


def normalize_visibility(visibility: str):
    candidate = visibility.strip().lower()
    if candidate not in VALID_VISIBILITIES:
        raise HTTPException(status_code=400, detail="Visibility must be private or public")

    return candidate


def can_view_video(video, current_user: AuthUser | None):
    if video.visibility == "public":
        return True

    return current_user is not None and video.owner_user_id == current_user.user_id


def require_video_access(video, current_user: AuthUser | None):
    if not can_view_video(video, current_user):
        raise HTTPException(status_code=404, detail="Video not found")

    return video


def require_video_owner(video, current_user: AuthUser):
    if video.owner_user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Video not found")

    return video


def video_artifact_keys(video):
    return [
        video.raw_path,
        video.processed_path,
        video.audio_path,
        video.transcript_path,
        video.segments_path,
        video.embedding_path,
        video.thumbnail_path,
        f"thumbnails/{video.video_id}.jpg",
        metrics_key(video.video_id),
    ]


@router.get("/videos/{video_id}/metrics")
def get_video_metrics(
    video_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_owner(video, current_user)

    return read_metrics(storage, video_id)


@router.delete("/videos/{video_id}")
def delete_video_endpoint(
    video_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_owner(video, current_user)

    db.refresh(video, with_for_update=True)
    if video.status in ACTIVE_DELETE_BLOCK_STATUSES or has_active_work(db, video_id):
        raise HTTPException(
            status_code=409,
            detail=f"Video is still {video.status}. Delete after processing finishes or fails."
        )

    video.status = "deleting"
    from app.db.search_repository import revoke_video
    revoke_video(db, video_id)
    db.commit()
    if os.getenv("ELASTICSEARCH_URL"):
        elastic = ElasticsearchSearch()
        try:
            elastic.delete(video_id)
        except (requests.RequestException, RuntimeError) as exc:
            raise HTTPException(503, "Search cleanup is unavailable; retry deleting this video") from exc
        finally:
            elastic.close()
    delete_segments(video_id)
    s3_deleted_count = storage.delete_many(
        video_artifact_keys(video)
        + list_search_chunk_artifact_keys(db, video_id)
    )
    for root in ("audio_chunks", "transcripts/chunks", "embeddings/chunks",
                 "transcripts/final", "embeddings/final", "processed_videos", "thumbnails"):
        s3_deleted_count += storage.delete_prefix(f"{root}/{video_id}/")
    delete_search_chunks(db, video_id)
    db.query(PipelineJob).filter_by(video_id=video_id).delete(synchronize_session=False)
    delete_video(db, video)

    return {
        "video_id": video_id,
        "status": "deleted",
        "s3_deleted_count": s3_deleted_count,
        "qdrant_deleted": True,
        "database_deleted": True,
    }


@router.get("/videos/{video_id}/segments")
def get_video_segments(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    if video.segments_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Segments not ready. Current status: {video.segments_status}"
        )

    if not storage.exists(video.segments_path):
        raise HTTPException(status_code=404, detail="Segments file not found")

    segments = storage.read_json(video.segments_path)

    return {
        "video_id": video_id,
        "segments": segments
    }

@router.get("/videos/{video_id}/transcripts")
def get_transcript(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    if video.transcript_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Transcript not ready. Current status: {video.transcript_status}"
        )

    if not storage.exists(video.transcript_path):
        raise HTTPException(status_code=404, detail="Transcript file not found")

    transcript_text = storage.read_text(video.transcript_path)

    return {
        "video_id": video_id,
        "transcript": transcript_text
    }


@router.post("/videos/{video_id}/search")
def search(
    video_id: str,
    request: SearchRequest,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    search_start = perf_counter()
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    if request.algorithm == "embedding":
        raise HTTPException(
            status_code=400,
            detail="File-based embedding search is not available with S3 storage. Use qdrant_embedding."
        )

    if video.status == "deleting":
        raise HTTPException(404, "Video not found")

    if request.algorithm in {"bm25", "hybrid", "elasticsearch_bm25", "elasticsearch_hybrid"}:
        if video.segments_status != "completed" or not video.segments_path:
            raise HTTPException(409, "Search segments are not ready")
        lexical_start = perf_counter()
        lexical_publication = None
        if request.algorithm.startswith("elasticsearch_"):
            elastic = None
            try:
                if not os.getenv("ELASTICSEARCH_URL"):
                    raise HTTPException(503, "Keyword search is not enabled")
                from app.db import search_repository
                if search_repository.enabled():
                    from app.services.projection_indexes import ElasticsearchProjectionIndex
                    lexical_publication = search_repository.published_lexical(db, video)
                    if lexical_publication is None:
                        raise IndexNotReady("Current revision is not indexed")
                    elastic = ElasticsearchProjectionIndex(lexical_publication["target"])
                    lexical = elastic.search(lexical_publication["attempt_id"], request.query, CANDIDATES)
                    # This endpoint is scoped to one video and still uses legacy
                    # dense hits. Library fusion will retain canonical identities.
                    lexical = [{key: hit[key] for key in ("start", "end", "text", "score")} for hit in lexical]
                else:
                    elastic = ElasticsearchSearch()
                    lexical = elastic.search(video_id, video.segments_path, request.query, CANDIDATES)
            except IndexNotReady as exc:
                raise HTTPException(409, "Keyword search is still indexing this video; try again shortly") from exc
            except (requests.RequestException, RuntimeError) as exc:
                raise HTTPException(503, "Elasticsearch is unavailable") from exc
            finally:
                if elastic:
                    elastic.close()
        else:
            lexical = BM25(storage.read_json(video.segments_path)).search(request.query, CANDIDATES)
        lexical_seconds = seconds_since(lexical_start)
        results = lexical[:5]
        dense_seconds = 0.0
        if request.algorithm in {"hybrid", "elasticsearch_hybrid"}:
            if video.embedding_status != "completed":
                raise HTTPException(409, "Embeddings are not ready")
            dense_start = perf_counter()
            chunks = list_search_chunks(db, video_id)
            dense = search_segments(encode_text(request.query), video_id, top_k=CANDIDATES,
                chunk_attempts=[(chunk.chunk_index, chunk.artifact_token) for chunk in chunks] if chunks else None)
            results = reciprocal_rank_fusion(dense, lexical)
            dense_seconds = seconds_since(dense_start)
        if request.algorithm.startswith("elasticsearch_"):
            revision = video.segments_path
            db.expire_all()
            current = get_video(db, video_id)
            if current is None or current.status == "deleting":
                raise HTTPException(404, "Video not found")
            require_video_access(current, current_user)
            if current.segments_path != revision or current.segments_status != "completed":
                raise HTTPException(409, "The transcript changed during search; please retry")
            if lexical_publication is not None and search_repository.published_lexical(db, current) != lexical_publication:
                raise HTTPException(409, "The search index changed during search; please retry")
        return {"video_id": video_id, "query": request.query, "algorithm": request.algorithm,
                "results": results, "metrics": {"search_latency_seconds": seconds_since(search_start),
                    "lexical_seconds": lexical_seconds, "dense_seconds": dense_seconds}}

    if request.algorithm == "qdrant_embedding":
        if video.embedding_status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Embeddings not ready. Current status: {video.embedding_status}"
            )

        query_embedding_start = perf_counter()
        query_embedding = encode_text(request.query)
        query_embedding_seconds = seconds_since(query_embedding_start)

        qdrant_start = perf_counter()
        chunks = list_search_chunks(db, video_id)
        results = search_segments(query_embedding, video_id,
            chunk_attempts=[(chunk.chunk_index, chunk.artifact_token) for chunk in chunks] if chunks else None)
        qdrant_search_seconds = seconds_since(qdrant_start)


        search_latency_seconds = seconds_since(search_start)
        safe_update_metrics(storage, video_id, "search", {
            "last_query": request.query,
            "search_latency_seconds": search_latency_seconds,
            "query_embedding_seconds": query_embedding_seconds,
            "qdrant_search_seconds": qdrant_search_seconds,
            "result_count": len(results),
            "top_score": results[0]["score"] if results else None,
        })
        return {
            "video_id": video_id,
            "query": request.query,
            "algorithm": request.algorithm,
            "results": results,
            "metrics": {
                        "search_latency_seconds": search_latency_seconds,
                        "query_embedding_seconds": query_embedding_seconds,
                        "qdrant_search_seconds": qdrant_search_seconds,
                    }
        }

    if video.segments_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Segments not ready. Current status: {video.segments_status}"
        )

    if not storage.exists(video.segments_path):
        raise HTTPException(status_code=404, detail="Segments file not found")

    segments = storage.read_json(video.segments_path)

    results = []

    for segment in segments:
        text = segment.get("text", "")
        score = score_segment(request.query, text, request.algorithm)

        if score > 0:
            results.append({
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": text,
                "score": score,
                "algorithm": request.algorithm
            })

    results.sort(key=lambda item: item["score"], reverse=True)

    return {
        "video_id": video_id,
        "query": request.query,
        "algorithm": request.algorithm,
        "results": results[:5]
    }

@router.get("/videos/{video_id}/file")
def get_video_file(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    if not video.processed_path:
        raise HTTPException(status_code=400, detail="Video playback is not ready yet")

    if not storage.exists(video.processed_path):
        raise HTTPException(status_code=404, detail="Processed video file not found")

    return storage.file_response(video.processed_path, media_type="video/mp4")


@router.get("/videos/{video_id}/file-url")
def get_video_file_url(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    if not video.processed_path:
        raise HTTPException(status_code=400, detail="Video playback is not ready yet")

    if not storage.exists(video.processed_path):
        raise HTTPException(status_code=404, detail="Processed video file not found")

    return {
        "video_id": video_id,
        "url": storage.presigned_url(video.processed_path, response_content_type="video/mp4")
    }


@router.get("/videos/{video_id}/thumbnail")
def get_video_thumbnail(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    thumbnail_key = video.thumbnail_path or f"thumbnails/{video_id}.jpg"
    if not storage.exists(thumbnail_key):
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return storage.file_response(thumbnail_key, media_type="image/jpeg")


@router.get("/videos/{video_id}/thumbnail-url")
def get_video_thumbnail_url(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    thumbnail_key = video.thumbnail_path or f"thumbnails/{video_id}.jpg"
    if not storage.exists(thumbnail_key):
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return {
        "video_id": video_id,
        "url": storage.presigned_url(thumbnail_key, response_content_type="image/jpeg")
    }


@router.get("/videos/{video_id}/status", response_model=VideoStatusResponse)
def get_video_status(
    video_id: str,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    require_video_access(video, current_user)

    return video_summary(video, current_user, VideoStatusResponse,
                         keyword_search_enabled=bool(os.getenv("ELASTICSEARCH_URL")))


@router.post("/videos/upload")
def upload_videos(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    visibility: str = Form("private"),
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):

    request_start = perf_counter()
    upload_accepted_at_epoch = now_epoch()

    suffix = Path(file.filename or "video").suffix
    video_title = normalize_title(title, file.filename)
    video_visibility = normalize_visibility(visibility)
    upsert_user(db, current_user)
    idempotency_key = idempotency_key or str(uuid4())
    fingerprint = upload_fingerprint(file.file, filename=file.filename, title=video_title,
        visibility=video_visibility, max_bytes=MAX_UPLOAD_BYTES)
    reservation, replay = reserve_upload(db, current_user.user_id, idempotency_key, fingerprint, suffix)
    if replay is not None:
        return replay
    video_id, token, raw_key = reservation.video_id, reservation.processing_token, reservation.raw_path
    db.rollback()
    saved_file = f"{video_id}{suffix}"

    s3_start = perf_counter()

    try:
        uploaded_bytes = storage.save_upload(file.file, raw_key, MAX_UPLOAD_BYTES)
    except Exception:
        abandon_upload(db, current_user.user_id, idempotency_key, token)
        raise

    s3_raw_upload_seconds = seconds_since(s3_start)

    enqueue_start = perf_counter()
    response = {"video_id": video_id, "title": video_title, "visibility": video_visibility,
                "filename": saved_file, "status": "uploaded", "idempotency_key": idempotency_key}
    commit_upload(db, current_user.user_id, idempotency_key, token, response,
                  new_video_record(response, current_user.user_id, raw_key))
    celery_enqueue_seconds = seconds_since(enqueue_start)

    upload_response_latency_seconds = seconds_since(request_start)

    safe_update_metrics(storage, video_id, "upload", {
        "accepted_at_epoch": upload_accepted_at_epoch,
        "upload_response_latency_seconds": upload_response_latency_seconds,
        "s3_raw_upload_seconds": s3_raw_upload_seconds,
        "uploaded_bytes": uploaded_bytes,
    })

    safe_update_metrics(storage, video_id, "upload", {
        "enqueued_at_epoch": now_epoch(),
        "celery_enqueue_seconds": celery_enqueue_seconds,
        "enqueue_backend": "postgres_outbox",
    })

    return response


def new_video_record(response, owner_user_id, raw_key):
    return {
        "video_id": response["video_id"],
        "owner_user_id": owner_user_id,
        "title": response["title"],
        "visibility": response["visibility"],
        "filename": response["filename"],
        "status": "uploaded",
        "raw_path": raw_key,
        "processed_path": None,
        "audio_path": None,
        "transcript_path": None,
        "segments_path": None,
        "embedding_path": None,
        "audio_status": "not_started",
        "transcript_status": "not_started",
        "segments_status": "not_started",
        "embedding_status": "not_started",
        "error_message": None,
    }


UPLOAD_SUFFIX_PATTERN = r"\.[A-Za-z0-9]{1,10}"


def upload_url_seconds(expires_at):
    """Presigned URLs never outlive the reservation that protects the raw key."""
    remaining = int((expires_at - datetime.utcnow()).total_seconds()) if expires_at else 0
    return max(1, min(int(os.getenv("UPLOAD_URL_SECONDS", "900")), remaining))


@router.post("/videos/uploads", response_model=CreateUploadResponse)
def create_direct_upload(
    request: CreateUploadRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Start a browser-to-S3 upload. The file never passes through the API.

    Call again with the same Idempotency-Key to get a fresh URL for the same
    attempt; after completion the stored result is replayed instead.
    """
    if request.size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Uploaded video is too large")
    content_type = request.content_type.strip().lower()
    if not re.fullmatch(r"video/[a-z0-9.+-]{1,60}", content_type):
        raise HTTPException(415, "Only video files can be uploaded")
    suffix = Path(request.filename).suffix
    if suffix and not re.fullmatch(UPLOAD_SUFFIX_PATTERN, suffix):
        raise HTTPException(400, "Unsupported file extension")
    video_title = normalize_title(request.title, request.filename)
    video_visibility = normalize_visibility(request.visibility)
    upsert_user(db, current_user)
    idempotency_key = idempotency_key or str(uuid4())
    fingerprint = upload_intent_fingerprint(filename=request.filename, title=video_title,
        visibility=video_visibility, size_bytes=request.size_bytes, content_type=content_type)
    reservation, replay = reserve_upload(
        db, current_user.user_id, idempotency_key, fingerprint, suffix, resume_inflight=True,
        pending_response={"title": video_title, "visibility": video_visibility, "status": "uploaded",
                          "idempotency_key": idempotency_key, "content_type": content_type,
                          "size_bytes": request.size_bytes})
    if replay is not None:
        return {"video_id": replay["video_id"], "idempotency_key": idempotency_key,
                "upload": None, "completed": UploadAccepted(**replay)}

    video_id, raw_key = reservation.video_id, reservation.raw_path
    seconds = upload_url_seconds(reservation.expires_at)
    pending = dict(reservation.response or {})
    db.rollback()
    post = storage.presigned_upload(raw_key, content_type=pending.get("content_type", content_type),
                                    size_bytes=pending.get("size_bytes", request.size_bytes), seconds=seconds)
    safe_update_metrics(storage, video_id, "upload", {"accepted_at_epoch": now_epoch(),
                                                      "upload_mode": "direct_s3"})
    return {"video_id": video_id, "idempotency_key": idempotency_key,
            "upload": {"url": post["url"], "fields": post["fields"]}, "expires_in_seconds": seconds}


@router.post("/videos/uploads/{video_id}/complete", response_model=UploadAccepted)
def complete_direct_upload(
    video_id: str,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Confirm the object reached S3, then create the video and queue processing."""
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    request_start = perf_counter()
    reservation, replay = find_direct_upload(db, current_user.user_id, idempotency_key, video_id)
    if replay is not None:
        return replay

    pending = dict(reservation.response or {})
    if "size_bytes" not in pending:
        raise HTTPException(409, "This Idempotency-Key belongs to a different upload method")
    size = storage.object_size(reservation.raw_path)
    if size is None:
        raise HTTPException(409, "The file has not reached storage yet; finish the upload and retry")
    if size != pending.get("size_bytes") or size > MAX_UPLOAD_BYTES:
        # S3 enforces the declared size, so a mismatch means a stale or foreign object.
        storage.delete_many([reservation.raw_path])
        abandon_upload(db, current_user.user_id, idempotency_key, reservation.processing_token)
        raise HTTPException(422, "Uploaded file size does not match the upload request")

    suffix = Path(reservation.raw_path).suffix
    response = {"video_id": video_id, "title": pending["title"], "visibility": pending["visibility"],
                "filename": f"{video_id}{suffix}", "status": "uploaded", "idempotency_key": idempotency_key}
    try:
        commit_upload(db, current_user.user_id, idempotency_key, reservation.processing_token, response,
                      new_video_record(response, current_user.user_id, reservation.raw_path))
    except HTTPException as exc:
        # A concurrent completion may have won; replay its result rather than failing.
        if exc.status_code != 409:
            raise
        _, replay = find_direct_upload(db, current_user.user_id, idempotency_key, video_id)
        if replay is None:
            raise
        return replay
    safe_update_metrics(storage, video_id, "upload", {
        "uploaded_bytes": size,
        "enqueued_at_epoch": now_epoch(),
        "upload_response_latency_seconds": seconds_since(request_start),
        "enqueue_backend": "postgres_outbox",
    })
    return response
# @router.post("/videos/upload")
# def upload_videos(file: UploadFile = File(...), db: Session = Depends(get_db)):
#     video_id = str(uuid4())

#     suffix = Path(file.filename).suffix
#     saved_file = f"{video_id}{suffix}"
#     save_path = RAW_VIDEO_DIR / saved_file

#     with save_path.open("wb") as buffer:
#         shutil.copyfileobj(file.file, buffer)

#     metadata = load_metadata()
#     metadata[video_id] = {
#         "video_id": video_id,
#         "filename": saved_file,
#         "status": "uploaded",
#         "raw_path": str(save_path),
#         "processed_filename": None,
#         "processed_path": None,
#         "audio_filename": None,
#         "audio_path": None,
#         "audio_status": "not_started",
#         "error_message": None,
#         "transcript_path": None,
#         "transcript_status": "not_started",
#         "segments_path": None,
#         "segments_status": "not_started",
#         "embedding_path": None,
#         "embedding_status": "not_started",
#     }

    

#     save_metadata(metadata)
#     # background_tasks.add_task(process_video, video_id)
#     process_video_task.delay(video_id)
#     return {
#         "video_id": video_id,
#         "filename": saved_file,
#         "status": "uploaded"
#     }
