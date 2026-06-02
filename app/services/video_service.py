import os
import shutil
import subprocess
from pathlib import Path

from tempfile import TemporaryDirectory
from app.services.storage_service import get_storage
from app.services.search_service import build_segment_embeddings

from app.db.session import create_session
from app.db.video_repository import get_video, update_video

from app.services.qdrant_service import upsert_segments
import whisper



from time import perf_counter
from app.services.metrics_service import now_epoch, read_metrics, seconds_since, update_metrics

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
    storage = get_storage()
    processing_start = perf_counter()

    try:
        video = get_video(db, video_id)
        if video is None:
            raise ValueError("Video not found")

        update_video(db, video_id, {"status": "processing"})

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            filename = video.filename
            raw_key = video.raw_path

            raw_path = temp_dir / filename
            processed_filename = f"processed_{filename}"
            processed_path = temp_dir / processed_filename
            audio_path = temp_dir / f"{video_id}.wav"

            storage.download_file(raw_key, raw_path)

            ffmpeg_path = get_ffmpeg_path()
            
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i", str(raw_path),
                    "-vf", "scale=-2:360",
                    str(processed_path),
                ],
                check=True,
            )

            thumbnail_path = temp_dir / f"{video_id}.jpg"
            thumbnail_generation_seconds = None
            try:
                thumbnail_start = perf_counter()
                subprocess.run(
                    [
                        ffmpeg_path,
                        "-y",
                        "-ss", "1",
                        "-i", str(processed_path),
                        "-frames:v", "1",
                        "-q:v", "3",
                        str(thumbnail_path),
                    ],
                    check=True,
                )
                thumbnail_generation_seconds = seconds_since(thumbnail_start)
            except Exception:
                thumbnail_path = None

            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i", str(processed_path),
                    "-vn",
                    "-acodec", "pcm_s16le",
                    "-ar", "16000",
                    "-ac", "1",
                    str(audio_path),
                ],
                check=True,
            )

            update_video(db, video_id, {
                "audio_status": "extracted",
                "transcript_status": "processing",
                "segments_status": "processing",
                "embedding_status": "processing",
            })
            
            # Whisper part 
            whisper_start = perf_counter()
            result = get_whisper_model().transcribe(str(audio_path))
            whisper_transcription_seconds = seconds_since(whisper_start)


            transcript_text = result["text"]
            segments = result["segments"]

            # Emebedding time
            embedding_start = perf_counter()
            segment_embeddings = build_segment_embeddings(segments)
            embedding_generation_seconds = seconds_since(embedding_start)  


            # Measure Qdrant upsert:
            qdrant_start = perf_counter()
            upsert_segments(video_id, segment_embeddings)
            qdrant_upsert_seconds = seconds_since(qdrant_start)


            processed_key = f"processed_videos/{processed_filename}"
            audio_key = f"audio/{video_id}.wav"
            thumbnail_key = f"thumbnails/{video_id}.jpg"
            transcript_key = f"transcripts/{video_id}.txt"
            segments_key = f"transcripts/{video_id}_segments.json"
            embedding_key = f"embeddings/{video_id}_embeddings.json"

            storage.upload_file(processed_path, processed_key)
            storage.upload_file(audio_path, audio_key)
            if thumbnail_path and thumbnail_path.exists():
                storage.upload_file(thumbnail_path, thumbnail_key)
            storage.write_json(embedding_key, segment_embeddings)
            storage.write_text(transcript_key, transcript_text)
            storage.write_json(segments_key, segments, indent=4)

            update_video(db, video_id, {
                "segments_path": segments_key,
                "segments_status": "completed",
                "transcript_status": "completed",
                "transcript_path": transcript_key,
                "status": "processed",
                "processed_path": processed_key,
                "audio_path": audio_key,
                "audio_status": "extracted",
                "embedding_path": embedding_key,
                "embedding_status": "completed",
            })
            
            try:
                metrics = read_metrics(storage, video_id)
                accepted_at_epoch = metrics.get("upload", {}).get("accepted_at_epoch")

                time_to_searchable_seconds = None
                if accepted_at_epoch:
                    time_to_searchable_seconds = round(now_epoch() - accepted_at_epoch, 4)

                update_metrics(storage, video_id, "processing", {
                    "time_to_searchable_seconds": time_to_searchable_seconds,
                    "whisper_transcription_seconds": whisper_transcription_seconds,
                    "embedding_generation_seconds": embedding_generation_seconds,
                    "qdrant_upsert_seconds": qdrant_upsert_seconds,
                    "thumbnail_generation_seconds": thumbnail_generation_seconds,
                    "total_processing_seconds": seconds_since(processing_start),
                })
            except Exception:
                pass
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
