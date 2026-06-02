
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, Depends

from pydantic import BaseModel
from app.services.search_service import score_segment
from app.workers.task import process_video_task

from app.db.session import get_db
from app.db.video_repository import create_video, get_video
from sqlalchemy.orm import Session

from app.services.qdrant_service import search_segments
from app.services.search_service import encode_text


from app.services.storage_service import S3Storage


from time import perf_counter
from app.services.metrics_service import now_epoch, seconds_since, update_metrics, read_metrics


storage = S3Storage()

router = APIRouter()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024


class SearchRequest(BaseModel):
    query: str
    algorithm: Literal[
    "embedding",
    "qdrant_embedding",
    "exact",
    "overlap",
    "stopword_overlap"
] = "qdrant_embedding"


@router.get("/videos/{video_id}/metrics")
def get_video_metrics(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    return read_metrics(storage, video_id)


@router.get("/videos/{video_id}/segments")
def get_video_segments(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

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
def get_transcript(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

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
def search(video_id: str, request: SearchRequest, db: Session = Depends(get_db)):
    search_start = perf_counter()
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    if request.algorithm == "embedding":
        raise HTTPException(
            status_code=400,
            detail="File-based embedding search is not available with S3 storage. Use qdrant_embedding."
        )

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
        results = search_segments(query_embedding, video_id)
        qdrant_search_seconds = seconds_since(qdrant_start)


        search_latency_seconds = seconds_since(search_start)
        update_metrics(storage, video_id, "search", {
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
def get_video_file(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.status != "processed":
        raise HTTPException(status_code=400, detail="Video not processed yet")

    if not storage.exists(video.processed_path):
        raise HTTPException(status_code=404, detail="Processed video file not found")

    return storage.file_response(video.processed_path, media_type="video/mp4")

@router.get("/videos/{video_id}/status")
def get_video_status(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    return video


@router.post("/videos/upload")
def upload_videos(file: UploadFile = File(...), db: Session = Depends(get_db)):

    request_start = perf_counter()
    upload_accepted_at_epoch = now_epoch()

    video_id = str(uuid4())

    suffix = Path(file.filename).suffix
    saved_file = f"{video_id}{suffix}"
    raw_key = f"raw_videos/{saved_file}"

    s3_start = perf_counter()

    try:
        uploaded_bytes = storage.save_upload(file.file, raw_key, MAX_UPLOAD_BYTES)
    except ValueError:
        raise HTTPException(status_code=413, detail="Uploaded video is too large")

    s3_raw_upload_seconds = seconds_since(s3_start)

    create_video(db, {
        "video_id": video_id,
        "filename": saved_file,
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
    })

    process_video_task.delay(video_id)
    upload_response_latency_seconds = seconds_since(request_start)

    update_metrics(storage, video_id, "upload", {
        "accepted_at_epoch": upload_accepted_at_epoch,
        "upload_response_latency_seconds": upload_response_latency_seconds,
        "s3_raw_upload_seconds": s3_raw_upload_seconds,
        "uploaded_bytes": uploaded_bytes,
    })

    return {
        "metrics": {
            "upload_response_latency_seconds": upload_response_latency_seconds,
            "s3_raw_upload_seconds": s3_raw_upload_seconds,
        },
        "video_id": video_id,
        "filename": saved_file,
        "status": "uploaded"
    }
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
