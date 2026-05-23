import json
import os
import shutil
import subprocess
from pathlib import Path

import whisper

from app.services.metadata_service import load_metadata, save_metadata
from app.services.search_service import build_segment_embeddings


RAW_VIDEO_DIR = Path("storage/raw_videos")
RAW_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

PROCESSED_VIDEO_DIR = Path("storage/processed_videos")
PROCESSED_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = Path("storage/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

TRANSCRIPT_DIR = Path("storage/transcripts")
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

EMBEDDING_DIR = Path("storage/embeddings")
EMBEDDING_DIR.mkdir(parents=True, exist_ok=True)

whisper_model = whisper.load_model("base")


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
        metadata[video_id]["segments_status"] = "processing"
        metadata[video_id]["embedding_status"] = "processing"
        save_metadata(metadata)

        transcript_path = TRANSCRIPT_DIR / f"{video_id}.txt"
        segments_path = TRANSCRIPT_DIR / f"{video_id}_segments.json"
        embedding_path = EMBEDDING_DIR / f"{video_id}_embeddings.json"

        result = whisper_model.transcribe(str(audio_path))
        transcript_text = result["text"]
        segments = result["segments"]
        segment_embeddings = build_segment_embeddings(segments)

        with embedding_path.open("w", encoding="utf-8") as file:
            json.dump(segment_embeddings, file)

        with transcript_path.open("w", encoding="utf-8") as file:
            file.write(transcript_text)

        with segments_path.open("w", encoding="utf-8") as file:
            json.dump(segments, file, indent=4)

        metadata = load_metadata()
        metadata[video_id]["segments_path"] = str(segments_path)
        metadata[video_id]["segments_status"] = "completed"
        metadata[video_id]["transcript_status"] = "completed"
        metadata[video_id]["transcript_path"] = str(transcript_path)
        metadata[video_id]["status"] = "processed"
        metadata[video_id]["embedding_path"] = str(embedding_path)
        metadata[video_id]["embedding_status"] = "completed"
        save_metadata(metadata)

    except Exception as e:
        metadata = load_metadata()
        metadata[video_id]["status"] = "failed"
        metadata[video_id]["error_message"] = str(e)
        if metadata[video_id].get("transcript_status") == "processing":
            metadata[video_id]["transcript_status"] = "failed"
        if metadata[video_id].get("segments_status") == "processing":
            metadata[video_id]["segments_status"] = "failed"
        if metadata[video_id].get("embedding_status") == "processing":
            metadata[video_id]["embedding_status"] = "failed"
        save_metadata(metadata)
