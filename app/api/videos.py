import json
import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, Depends
from pydantic import BaseModel

from app.services.search_service import embedding_search, score_segment
from app.services.video_service import RAW_VIDEO_DIR
from app.workers.task import process_video_task

from app.db.session import get_db
from app.db.video_repository import create_video, get_video
from sqlalchemy.orm import Session
router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    algorithm: Literal["embedding", "exact", "overlap", "stopword_overlap"] = "embedding"


@router.get("/videos/{video_id}/segments")
def get_video_segments(video_id: str,db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.segments_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Segments not ready. Current status: {video.segments_status}"
        )
    segments_path = Path(video.segments_path)

    if not segments_path.exists():
        raise HTTPException(status_code=404, detail="Segments file not found")

    with segments_path.open("r", encoding="utf-8") as file:
        segments = json.load(file)

    return {
        "video_id": video_id,
        "segments": segments
    }


@router.post("/videos/{video_id}/search")
def search(video_id: str, request: SearchRequest,db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")


    if request.algorithm == "embedding":
        if video.embedding_status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Embeddings not ready. Current status: {video.embedding_status}"
            )
        
        embedding_path = Path(video.embedding_path)

        if not embedding_path.exists():
            raise HTTPException(status_code=404, detail="Embedding file not found")

        results = embedding_search(request.query, embedding_path)

        return {
            "video_id": video_id,
            "query": request.query,
            "algorithm": request.algorithm,
            "results": results
        }

    if video.segments_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Segments not ready. Current status: {video.segments_status}"
        )
    segments_path = Path(video.segments_path)

    if not segments_path.exists():
        raise HTTPException(status_code=404, detail="Segments file not found")

    with segments_path.open("r", encoding="utf-8") as file:
        segments = json.load(file)

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






@router.get("/videos/{video_id}/transcripts")
def get_transcript(video_id: str,db: Session = Depends(get_db)):
    video = get_video(db, video_id)
    # metadata = load_metadata()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    
    # if video_id not in metadata:
    #     raise HTTPException(status_code=404, detail="Video not found")

    if video.transcript_status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Transcript not ready. Current status: {video.transcript_status}"
        )
    
    # if metadata[video_id].get("transcript_status") != "completed":
    #     raise HTTPException(
    #         status_code=400,
    #         detail=f"Transcript not ready. Current status: {metadata[video_id].get('transcript_status')}"
    #     )
    transcript_path = Path(video.transcript_path)
    # transcript_path = Path(metadata[video_id]["transcript_path"])

    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Transcript file not found")

    with transcript_path.open("r", encoding="utf-8") as file:
        transcript_text = file.read()

    return {
        "video_id": video_id,
        "transcript": transcript_text
    }


@router.get("/videos/{video_id}/status")
def get_video_status(video_id: str, db: Session = Depends(get_db)):
    video = get_video(db, video_id)

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    return video

# @router.get("/videos/{video_id}/status")
# def get_video_status(video_id: str):
#     metadata = load_metadata()

#     if video_id not in metadata:
#         raise HTTPException(status_code=404, detail="Video not found")

#     return metadata[video_id]



@router.post("/videos/upload")
def upload_videos(file: UploadFile = File(...), db: Session = Depends(get_db)):
    video_id = str(uuid4())

    suffix = Path(file.filename).suffix
    saved_file = f"{video_id}{suffix}"
    save_path = RAW_VIDEO_DIR / saved_file

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    create_video(db, {
        "video_id": video_id,
        "filename": saved_file,
        "status": "uploaded",
        "raw_path": str(save_path),
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

    return {
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
