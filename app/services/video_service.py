import json
import os
import shutil
import subprocess
from pathlib import Path


from app.core.storage import (
    AUDIO_DIR,
    EMBEDDING_DIR,
    PROCESSED_VIDEO_DIR,
    TRANSCRIPT_DIR,
)
from app.services.search_service import build_segment_embeddings

from app.db.session import create_session
from app.db.video_repository import get_video, update_video

from app.services.qdrant_service import upsert_segments
import whisper

_whisper_model = None


def get_whisper_model():
    global _whisper_model

    if _whisper_model is None:
        model_name = os.getenv("WHISPER_MODEL", "base")
        _whisper_model = whisper.load_model(model_name)

    return _whisper_model


def add_ffmpeg_to_path(ffmpeg_path):
    ffmpeg_dir = str(Path(ffmpeg_path).parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)

    if ffmpeg_dir not in path_parts:
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


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


def process_video(video_id):
    db = create_session()

    try:
        video = get_video(db, video_id)

        if video is None:
            raise ValueError("Video not found")

        update_video(db, video_id, {
            "status": "processing"
        })

        raw_path = video.raw_path
        filename = video.filename

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
        
        update_video(db, video_id, {
            "audio_path": str(audio_path),
            "audio_status": "extracted",
            "processed_path": str(processed_path),
        })
        
        update_video(db, video_id, {
            "transcript_status": "processing",
            "segments_status": "processing",
            "embedding_status": "processing",
        })

        # metadata = load_metadata()
        # metadata[video_id]["audio_filename"] = audio_filename
        # metadata[video_id]["audio_path"] = str(audio_path)
        # metadata[video_id]["audio_status"] = "extracted"
        # metadata[video_id]["processed_filename"] = processed_filename
        # metadata[video_id]["processed_path"] = str(processed_path)
        # save_metadata(metadata)

        # metadata = load_metadata()
        # metadata[video_id]["transcript_status"] = "processing"
        # metadata[video_id]["segments_status"] = "processing"
        # metadata[video_id]["embedding_status"] = "processing"
        # save_metadata(metadata)

        transcript_path = TRANSCRIPT_DIR / f"{video_id}.txt"
        segments_path = TRANSCRIPT_DIR / f"{video_id}_segments.json"
        embedding_path = EMBEDDING_DIR / f"{video_id}_embeddings.json"

        result = get_whisper_model().transcribe(str(audio_path))
        transcript_text = result["text"]
        segments = result["segments"]
        segment_embeddings = build_segment_embeddings(segments)
        upsert_segments(video_id, segment_embeddings)


        with embedding_path.open("w", encoding="utf-8") as file:
            json.dump(segment_embeddings, file)

        with transcript_path.open("w", encoding="utf-8") as file:
            file.write(transcript_text)

        with segments_path.open("w", encoding="utf-8") as file:
            json.dump(segments, file, indent=4)
        
        update_video(db, video_id, {
            "segments_path": str(segments_path),
            "segments_status": "completed",
            "transcript_status": "completed",
            "transcript_path": str(transcript_path),
            "status": "processed",
            "embedding_path": str(embedding_path),
            "embedding_status": "completed",
        })


    except Exception as e:
        update_video(db, video_id, {
            "status": "failed",
            "error_message": str(e),
            "transcript_status": "failed",
            "segments_status": "failed",
            "embedding_status": "failed",
        })
        raise

    finally:
        db.close()
