from fastapi import FastAPI, UploadFile, File, APIRouter, HTTPException, BackgroundTasks
from pathlib import Path
from uuid import uuid4
import os
import shutil
import subprocess
import json
import whisper

whisper_model = whisper.load_model("base")

RAW_VIDEO_DIR  = Path("storage/raw_videos")
RAW_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

METADATA_PATH = Path("storage/videos.json")
METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)

PROCESSED_VIDEO_DIR = Path("storage/processed_videos")
PROCESSED_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = Path("storage/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

TRANSCRIPT_DIR = Path("storage/transcripts")
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()

def get_ffmpeg_path():
    ffmpeg_path = shutil.which("ffmpeg")

    if ffmpeg_path:
        add_ffmpeg_to_path(ffmpeg_path)
        return ffmpeg_path

    configured_path = os.getenv("FFMPEG_PATH") or os.getenv("FFMPEG_BINARY")
    if configured_path and Path(configured_path).exists():
        add_ffmpeg_to_path(configured_path)
        return configured_path

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        for candidate in winget_dir.glob("Gyan.FFmpeg_Microsoft.Winget.Source_*\\ffmpeg-*\\bin\\ffmpeg.exe"):
            if candidate.exists():
                add_ffmpeg_to_path(str(candidate))
                return str(candidate)

    raise RuntimeError("ffmpeg not found. Make sure ffmpeg is installed and added to PATH.")

def add_ffmpeg_to_path(ffmpeg_path):
    ffmpeg_dir = str(Path(ffmpeg_path).parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)

    if ffmpeg_dir not in path_parts:
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")



def load_metadata():
    if not METADATA_PATH.exists():
        return {}

    with METADATA_PATH.open("r") as file:
        return json.load(file)
    
def save_metadata(metadata):
    with METADATA_PATH.open("w") as file:
        json.dump(metadata, file, indent=4)


def process_video(video_id):
    try:
        metadata = load_metadata()

        metadata[video_id]["status"] = "processing"
        save_metadata(metadata)

        raw_path = metadata[video_id]["raw_path"]
        filename = metadata[video_id]["filename"]

        processed_filename = f"processed_{filename}"
        processed_path = PROCESSED_VIDEO_DIR / processed_filename


        ffmpeg_path = get_ffmpeg_path()
        subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i", raw_path,
                "-vf", "scale=-2:360",
                str(processed_path)
            ],
            check=True
        )

        audio_filename = f"{video_id}.wav"
        audio_path = AUDIO_DIR / audio_filename

        subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i", str(processed_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(audio_path)
            ],
            check=True
            )

        metadata = load_metadata()
        metadata[video_id]["audio_filename"] = audio_filename
        metadata[video_id]["audio_path"] = str(audio_path)
        metadata[video_id]["audio_status"] = "extracted"
        metadata[video_id]["processed_filename"] = processed_filename
        metadata[video_id]["processed_path"] = str(processed_path)
        save_metadata(metadata)

        metadata = load_metadata()
        metadata[video_id]["transcript_status"] = "processing"
        save_metadata(metadata)

        transcript_filename = f"{video_id}.txt"
        transcript_path = TRANSCRIPT_DIR / transcript_filename

        segment_filename = f"{video_id}_segments.txt"
        segments_path = TRANSCRIPT_DIR / segment_filename



        result = whisper_model.transcribe(str(audio_path))
        transcript_text = result["text"]
        segments = result["segments"]

        with transcript_path.open("w", encoding="utf-8") as file:
            file.write(transcript_text)
        
        with segments_path.open("w", encoding="utf-8") as file:
            file.write(segments)


        metadata = load_metadata()
        metadata[video_id]["segments_path"] = str(segments_path)
        metadata[video_id]["segments_status"] = "completed"
        metadata[video_id]["transcript_status"] = "completed"
        metadata[video_id]["transcript_path"] = str(transcript_path)
        metadata[video_id]["status"] = "processed"
        save_metadata(metadata)


    except Exception as e:
        metadata = load_metadata()
        metadata[video_id]["status"] = "failed"
        metadata[video_id]["error_message"] = str(e)
        if metadata[video_id].get("transcript_status") == "processing":
            metadata[video_id]["transcript_status"] = "failed"
        save_metadata(metadata)

@router.get("/videos/{video_id}/segments")
def get_video_segments(video_id: str):
    metadata = load_metadata()

    if video_id not in metadata:
        raise HTTPException(status_code=404, detail="Video not found")

    video = metadata[video_id]

    if video.get("segments_status") != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Segments not ready. Current status: {video.get('segments_status')}"
        )

    segments_path = Path(video["segments_path"])

    if not segments_path.exists():
        raise HTTPException(status_code=404, detail="Segments file not found")

    with segments_path.open("r", encoding="utf-8") as file:
        segments = json.load(file)

    return {
        "video_id": video_id,
        "segments": segments
    }
"""
@router.post("/videos/{video_id}/search")
def search():
"""


@router.get("/videos/{video_id}/status")
def get_video_status(video_id: str):
    metadata = load_metadata()

    if video_id not in metadata:
        raise HTTPException(status_code=404, detail="Video not found")

    return metadata[video_id]

@router.get("/videos/{video_id}/transcripts")
def get_transcript(video_id):
    metadata = load_metadata()

    if video_id not in metadata:
        raise HTTPException(status_code = 404, detail = "video not found")
                    
    if metadata[video_id]["transcript_status"] == "failed":
        raise HTTPException(status_code = 404, detail = "Previous transcript failed, current status {video[]}")
    
    transcript_path = metadata[video_id]["transcript_path"]


    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Transcript file not found")

    with transcript_path.open("r",encoding="utf-8") as file:
        transcript_text = file.read()

    return {
        "video_id": video_id,
        "transcript": transcript_text
    }

@router.post("/videos/upload")
def upload_videos(background_tasks: BackgroundTasks,file: UploadFile = File(...)):
    video_id = str(uuid4())

    suffix = Path(file.filename).suffix
    saved_file = f"{video_id}{suffix}"
    save_path = RAW_VIDEO_DIR/saved_file

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    metadata = load_metadata()


    metadata[video_id] = {
        "video_id": video_id,
        "filename": saved_file,
        "status": "uploaded",
        "raw_path": str(save_path),
        "processed_filename": None,
        "processed_path": None,
        "audio_filename": None,
        "audio_path": None,
        "audio_status": "not_started",
        "error_message": None,
        "transcript_path": None,
        "transcript_status": "not_started",
        "segments_path": None,
        "segments_status": "not_started",
    }   

    save_metadata(metadata)


    background_tasks.add_task(process_video,video_id)

    # 4. return response
    return {
        "video_id": video_id,
        "filename": saved_file,
        "status": "uploaded"
    }