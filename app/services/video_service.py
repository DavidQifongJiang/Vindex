import os
import math
import shutil
import subprocess
from pathlib import Path

from tempfile import TemporaryDirectory
from app.services.storage_service import get_storage
from app.services.search_service import build_segment_embeddings, get_embedding_model

from app.db.session import create_session
from app.db.video_repository import (
    claim_search_chunk,
    create_search_chunks,
    delete_search_chunks,
    get_video,
    get_video_for_processing_attempt,
    get_search_chunk,
    list_search_chunks,
    mark_video_processed_if_ready,
    mark_video_search_failed,
    record_search_chunk_success,
    record_processing_success,
    search_chunk_counts,
    update_video,
    update_video_for_processing_attempt,
)

from app.services.processing_errors import (
    PermanentProcessingError,
    StaleProcessingAttempt,
    classify_processing_exception,
)
from app.services.qdrant_service import delete_segments, upsert_segments
from app.services.retry_policy import MAX_PROCESSING_ATTEMPTS
import whisper



from time import perf_counter
from app.services.metrics_service import now_epoch, read_metrics, seconds_since, safe_update_metrics

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


def get_ffprobe_path(ffmpeg_path: str | None = None):
    ffprobe_path = shutil.which("ffprobe")

    if ffprobe_path:
        return ffprobe_path

    if ffmpeg_path:
        ffmpeg_candidate = Path(ffmpeg_path)
        executable_name = "ffprobe.exe" if ffmpeg_candidate.suffix.lower() == ".exe" else "ffprobe"
        sibling = ffmpeg_candidate.with_name(executable_name)
        if sibling.exists():
            return str(sibling)

    configured_path = os.getenv("FFPROBE_PATH")
    if configured_path and Path(configured_path).exists():
        return configured_path

    raise RuntimeError("ffprobe not found. Make sure ffprobe is installed and added to PATH.")


def probe_duration_seconds(video_path: Path, ffmpeg_path: str):
    ffprobe_path = get_ffprobe_path(ffmpeg_path)

    try:
        output = subprocess.check_output(
            [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            text=True,
        )
    except Exception as exc:
        raise_classified(
            exc,
            message="Could not inspect video duration",
            error_code="video_duration_probe_failed",
        )

    try:
        duration = float(output.strip())
    except ValueError as exc:
        raise PermanentProcessingError(
            "Could not parse video duration",
            error_code="video_duration_parse_failed",
            cause=exc,
        ) from exc

    if duration <= 0:
        raise PermanentProcessingError(
            "Video duration must be greater than zero",
            error_code="invalid_video_duration",
        )

    return duration


def search_chunk_seconds():
    value = float(os.getenv("SEARCH_CHUNK_SECONDS", "300"))
    if value <= 0:
        raise PermanentProcessingError(
            "SEARCH_CHUNK_SECONDS must be greater than zero",
            error_code="invalid_search_chunk_seconds",
        )
    return value


def search_chunk_overlap_seconds():
    value = float(os.getenv("SEARCH_CHUNK_OVERLAP_SECONDS", "5"))
    if value < 0:
        raise PermanentProcessingError(
            "SEARCH_CHUNK_OVERLAP_SECONDS must be zero or greater",
            error_code="invalid_search_chunk_overlap_seconds",
        )
    return value


def build_search_chunk_specs(duration_seconds: float):
    chunk_seconds = search_chunk_seconds()
    overlap_seconds = search_chunk_overlap_seconds()
    chunk_count = max(1, math.ceil(duration_seconds / chunk_seconds))
    chunks = []

    for index in range(chunk_count):
        start_seconds = index * chunk_seconds
        end_seconds = min(duration_seconds, start_seconds + chunk_seconds)
        audio_start_seconds = max(0.0, start_seconds - overlap_seconds)
        audio_end_seconds = min(duration_seconds, end_seconds + overlap_seconds)

        chunks.append({
            "chunk_index": index,
            "start_seconds": round(start_seconds, 4),
            "end_seconds": round(end_seconds, 4),
            "audio_start_seconds": round(audio_start_seconds, 4),
            "audio_end_seconds": round(audio_end_seconds, 4),
        })

    return chunks


def ffmpeg_seconds(value: float):
    return f"{value:.3f}"


def adjusted_chunk_segments(raw_segments, chunk):
    adjusted = []
    chunk_index = chunk.chunk_index
    logical_start = float(chunk.start_seconds or 0)
    logical_end = float(chunk.end_seconds or 0)
    audio_start = float(chunk.audio_start_seconds or logical_start)

    for local_index, segment in enumerate(raw_segments):
        local_start = float(segment.get("start") or 0)
        local_end = float(segment.get("end") or local_start)
        global_start = audio_start + local_start
        global_end = audio_start + local_end
        midpoint = (global_start + global_end) / 2

        if midpoint < logical_start:
            continue
        if midpoint >= logical_end and global_end > logical_end:
            continue

        text = (segment.get("text") or "").strip()
        if not text:
            continue

        adjusted.append({
            "segment_id": f"{chunk_index}:{local_index}",
            "chunk_index": chunk_index,
            "start": round(max(logical_start, global_start), 3),
            "end": round(min(logical_end, global_end), 3),
            "text": text,
        })

    return adjusted


def sum_chunk_metric(chunks, attribute: str):
    total = 0.0
    found = False

    for chunk in chunks:
        value = getattr(chunk, attribute, None)
        if value is None:
            continue
        total += float(value)
        found = True

    if not found:
        return None

    return round(total, 4)


def ensure_current_attempt(db, video_id: str, processing_token: str):
    video = get_video_for_processing_attempt(db, video_id, processing_token)

    if video is None:
        raise StaleProcessingAttempt(
            "Processing attempt no longer owns this video",
            error_code="stale_processing_attempt",
        )

    return video


def update_attempt(db, video_id: str, processing_token: str, updates: dict):
    video = update_video_for_processing_attempt(db, video_id, processing_token, updates)

    if video is None:
        raise StaleProcessingAttempt(
            "Processing attempt no longer owns this video",
            error_code="stale_processing_attempt",
        )

    return video


def raise_classified(error: Exception, *, message: str, error_code: str):
    raise classify_processing_exception(
        error,
        default_message=message,
        default_error_code=error_code,
    ) from error


def seconds_from_upload(storage, video_id: str):
    metrics = read_metrics(storage, video_id)
    accepted_at_epoch = metrics.get("upload", {}).get("accepted_at_epoch")

    if not accepted_at_epoch:
        return None

    return round(now_epoch() - accepted_at_epoch, 4)


def record_full_processing_if_ready(db, storage, video_id: str):
    video = mark_video_processed_if_ready(db, video_id)

    if video is None or video.status != "processed":
        return video

    try:
        safe_update_metrics(storage, video_id, "processing", {
            "completed_at_epoch": now_epoch(),
            "total_processing_seconds": seconds_from_upload(storage, video_id),
        })
    except Exception:
        pass

    return video


def prepare_video_search_chunks(video_id):
    db = create_session()
    storage = get_storage()
    prepare_start = perf_counter()

    try:
        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        if video.embedding_status == "completed":
            record_full_processing_if_ready(db, storage, video_id)
            return {
                "video_id": video_id,
                "status": "already_completed",
                "chunk_indexes": [],
                "chunk_count": 0,
            }

        existing_chunks = list_search_chunks(db, video_id)
        if existing_chunks:
            finalize_chunked_search_if_ready(video_id)
            chunk_indexes = [
                chunk.chunk_index
                for chunk in existing_chunks
                if chunk.status in {"pending", "retrying"}
            ]
            return {
                "video_id": video_id,
                "status": "already_prepared",
                "chunk_indexes": chunk_indexes,
                "chunk_count": len(existing_chunks),
            }

        update_video(db, video_id, {
            "status": "processing",
            "audio_status": "processing",
            "transcript_status": "processing",
            "segments_status": "processing",
            "embedding_status": "processing",
            "error_message": None,
        })

        try:
            safe_update_metrics(storage, video_id, "processing", {
                "search_architecture": "chunked",
                "search_started_at_epoch": now_epoch(),
                "search_queue_wait_seconds": seconds_from_upload(storage, video_id),
            })
        except Exception:
            pass

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            raw_path = temp_dir / video.filename
            raw_download_start = perf_counter()
            try:
                storage.download_file(video.raw_path, raw_path)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not download raw video from S3",
                    error_code="raw_video_download_failed",
                )
            search_raw_download_seconds = seconds_since(raw_download_start)

            try:
                ffmpeg_path = get_ffmpeg_path()
            except Exception as exc:
                raise PermanentProcessingError(
                    "FFmpeg is not available in the worker",
                    error_code="ffmpeg_not_available",
                    cause=exc,
                ) from exc

            duration_probe_start = perf_counter()
            duration_seconds = probe_duration_seconds(raw_path, ffmpeg_path)
            video_duration_probe_seconds = seconds_since(duration_probe_start)
            chunk_specs = build_search_chunk_specs(duration_seconds)

            prepared_chunks = []
            for chunk in chunk_specs:
                chunk_index = chunk["chunk_index"]
                audio_key = f"audio_chunks/{video_id}/{chunk_index:04d}.wav"
                audio_path = temp_dir / f"{video_id}_{chunk_index:04d}.wav"
                audio_duration = chunk["audio_end_seconds"] - chunk["audio_start_seconds"]

                audio_extraction_start = perf_counter()
                try:
                    subprocess.run(
                        [
                            ffmpeg_path,
                            "-y",
                            "-ss", ffmpeg_seconds(chunk["audio_start_seconds"]),
                            "-i", str(raw_path),
                            "-t", ffmpeg_seconds(audio_duration),
                            "-vn",
                            "-acodec", "pcm_s16le",
                            "-ar", "16000",
                            "-ac", "1",
                            str(audio_path),
                        ],
                        check=True,
                    )
                except Exception as exc:
                    raise_classified(
                        exc,
                        message="Audio chunk extraction failed",
                        error_code="audio_chunk_extraction_failed",
                    )
                audio_extraction_seconds = seconds_since(audio_extraction_start)

                audio_upload_start = perf_counter()
                try:
                    storage.upload_file(audio_path, audio_key)
                except Exception as exc:
                    raise_classified(
                        exc,
                        message="Could not upload audio chunk to S3",
                        error_code="audio_chunk_upload_failed",
                    )
                s3_audio_upload_seconds = seconds_since(audio_upload_start)

                prepared_chunks.append({
                    **chunk,
                    "audio_path": audio_key,
                    "audio_extraction_seconds": audio_extraction_seconds,
                    "s3_audio_upload_seconds": s3_audio_upload_seconds,
                    "max_processing_attempts": MAX_PROCESSING_ATTEMPTS,
                })

            try:
                delete_segments(video_id)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not clear old Qdrant vectors before chunked indexing",
                    error_code="qdrant_vector_delete_failed",
                )

            delete_search_chunks(db, video_id)
            chunks = create_search_chunks(db, video_id, prepared_chunks)
            update_video(db, video_id, {
                "audio_path": f"audio_chunks/{video_id}/",
                "audio_status": "extracted",
            })

            try:
                safe_update_metrics(storage, video_id, "processing", {
                    "search_chunk_count": len(chunks),
                    "search_chunk_seconds": search_chunk_seconds(),
                    "search_chunk_overlap_seconds": search_chunk_overlap_seconds(),
                    "video_duration_seconds": round(duration_seconds, 4),
                    "video_duration_probe_seconds": video_duration_probe_seconds,
                    "search_raw_download_seconds": search_raw_download_seconds,
                    "s3_raw_download_seconds": search_raw_download_seconds,
                    "audio_extraction_seconds": sum_chunk_metric(chunks, "audio_extraction_seconds"),
                    "s3_audio_upload_seconds": sum_chunk_metric(chunks, "s3_audio_upload_seconds"),
                    "search_chunk_preparation_seconds": seconds_since(prepare_start),
                })
            except Exception:
                pass

            return {
                "video_id": video_id,
                "status": "prepared",
                "chunk_indexes": [chunk.chunk_index for chunk in chunks],
                "chunk_count": len(chunks),
            }
    finally:
        db.close()


def process_video_search_chunk(
    video_id: str,
    chunk_index: int,
    processing_owner: str,
    max_processing_attempts: int = MAX_PROCESSING_ATTEMPTS,
):
    db = create_session()
    storage = get_storage()
    chunk_start = perf_counter()

    try:
        claimed_chunk = claim_search_chunk(
            db,
            video_id,
            chunk_index,
            processing_owner,
            max_processing_attempts,
        )

        if claimed_chunk is None:
            existing_chunk = get_search_chunk(db, video_id, chunk_index)
            if existing_chunk is not None and existing_chunk.status == "completed":
                finalize_chunked_search_if_ready(video_id)
                return {
                    "video_id": video_id,
                    "chunk_index": chunk_index,
                    "status": "already_completed",
                }

            return {
                "video_id": video_id,
                "chunk_index": chunk_index,
                "status": "skipped",
            }

        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        if video.embedding_status == "completed":
            return {
                "video_id": video_id,
                "chunk_index": chunk_index,
                "status": "search_already_completed",
            }

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            audio_path = temp_dir / Path(claimed_chunk.audio_path).name

            audio_download_start = perf_counter()
            try:
                storage.download_file(claimed_chunk.audio_path, audio_path)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not download audio chunk from S3",
                    error_code="audio_chunk_download_failed",
                )
            s3_audio_download_seconds = seconds_since(audio_download_start)

            whisper_model_start = perf_counter()
            try:
                whisper_model = get_whisper_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper model could not be loaded",
                    error_code="whisper_model_load_failed",
                )
            whisper_model_load_or_get_seconds = seconds_since(whisper_model_start)

            whisper_start = perf_counter()
            try:
                result = whisper_model.transcribe(str(audio_path))
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper chunk transcription failed",
                    error_code="whisper_chunk_transcription_failed",
                )
            whisper_transcription_seconds = seconds_since(whisper_start)

            segments = adjusted_chunk_segments(result.get("segments", []), claimed_chunk)
            transcript_text = " ".join(segment["text"] for segment in segments).strip()

            embedding_model_start = perf_counter()
            try:
                get_embedding_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding model could not be loaded",
                    error_code="embedding_model_load_failed",
                )
            embedding_model_load_or_get_seconds = seconds_since(embedding_model_start)

            embedding_start = perf_counter()
            try:
                segment_embeddings = build_segment_embeddings(segments)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding generation failed",
                    error_code="embedding_generation_failed",
                )
            embedding_generation_seconds = seconds_since(embedding_start)

            qdrant_start = perf_counter()
            if segment_embeddings:
                try:
                    upsert_segments(video_id, segment_embeddings)
                except Exception as exc:
                    raise_classified(
                        exc,
                        message="Qdrant chunk upsert failed",
                        error_code="qdrant_chunk_upsert_failed",
                    )
            qdrant_upsert_seconds = seconds_since(qdrant_start)

            transcript_key = f"transcripts/chunks/{video_id}/{chunk_index:04d}.txt"
            segments_key = f"transcripts/chunks/{video_id}/{chunk_index:04d}_segments.json"
            embedding_key = f"embeddings/chunks/{video_id}/{chunk_index:04d}_embeddings.json"

            artifact_write_start = perf_counter()
            try:
                storage.write_text(transcript_key, transcript_text)
                storage.write_json(segments_key, segments, indent=4)
                storage.write_json(embedding_key, segment_embeddings)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload chunk search artifacts to S3",
                    error_code="chunk_search_artifact_upload_failed",
                )
            s3_artifact_write_seconds = seconds_since(artifact_write_start)

            updated_chunk = record_search_chunk_success(
                db,
                video_id,
                chunk_index,
                {
                    "transcript_path": transcript_key,
                    "segments_path": segments_key,
                    "embedding_path": embedding_key,
                    "segment_count": len(segments),
                    "transcript_character_count": len(transcript_text),
                    "s3_audio_download_seconds": s3_audio_download_seconds,
                    "whisper_model_load_or_get_seconds": whisper_model_load_or_get_seconds,
                    "whisper_transcription_seconds": whisper_transcription_seconds,
                    "embedding_model_load_or_get_seconds": embedding_model_load_or_get_seconds,
                    "embedding_generation_seconds": embedding_generation_seconds,
                    "qdrant_upsert_seconds": qdrant_upsert_seconds,
                    "s3_artifact_write_seconds": s3_artifact_write_seconds,
                    "processing_seconds": seconds_since(chunk_start),
                },
            )

            if updated_chunk is None:
                raise StaleProcessingAttempt(
                    "Search chunk attempt no longer owns this chunk",
                    error_code="stale_search_chunk_attempt",
                )

        finalize_chunked_search_if_ready(video_id)

        return {
            "video_id": video_id,
            "chunk_index": chunk_index,
            "status": "processed",
        }
    finally:
        db.close()


def finalize_chunked_search_if_ready(video_id: str):
    db = create_session()
    storage = get_storage()
    finalize_start = perf_counter()

    try:
        counts = search_chunk_counts(db, video_id)
        if counts["total"] == 0:
            return None

        if counts["failed"] > 0:
            failed_chunk = (
                chunk
                for chunk in list_search_chunks(db, video_id)
                if chunk.status == "failed"
            )
            failed_chunk = next(failed_chunk, None)
            error_code = failed_chunk.last_error_code if failed_chunk else "search_chunk_failed"
            error_type = failed_chunk.last_error_type if failed_chunk else "SearchChunkFailed"
            error_message = (
                failed_chunk.last_error_message
                if failed_chunk
                else "One or more search chunks failed"
            )
            video = mark_video_search_failed(
                db,
                video_id,
                error_code=error_code or "search_chunk_failed",
                error_type=error_type or "SearchChunkFailed",
                error_message=error_message or "One or more search chunks failed",
            )
            try:
                safe_update_metrics(storage, video_id, "processing", {
                    "search_failed_at_epoch": now_epoch(),
                    "search_failed_chunks": counts["failed"],
                    "search_completed_chunks": counts["completed"],
                })
            except Exception:
                pass
            return video

        if counts["completed"] < counts["total"]:
            try:
                safe_update_metrics(storage, video_id, "processing", {
                    "search_completed_chunks": counts["completed"],
                    "search_processing_chunks": counts["processing"],
                    "search_pending_chunks": counts["pending"],
                    "search_retrying_chunks": counts["retrying"],
                    "search_chunk_count": counts["total"],
                })
            except Exception:
                pass
            return None

        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        if video.embedding_status == "completed":
            return record_full_processing_if_ready(db, storage, video_id)

        chunks = list_search_chunks(db, video_id)
        transcript_parts = []
        all_segments = []
        all_embeddings = []

        for chunk in chunks:
            try:
                transcript_parts.append(storage.read_text(chunk.transcript_path).strip())
                all_segments.extend(storage.read_json(chunk.segments_path))
                all_embeddings.extend(storage.read_json(chunk.embedding_path))
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not read completed chunk artifacts",
                    error_code="completed_chunk_artifact_read_failed",
                )

        all_segments.sort(key=lambda segment: (segment.get("start") or 0, segment.get("end") or 0))
        all_embeddings.sort(key=lambda segment: (segment.get("start") or 0, segment.get("end") or 0))
        transcript_text = "\n".join(part for part in transcript_parts if part)

        transcript_key = f"transcripts/{video_id}.txt"
        segments_key = f"transcripts/{video_id}_segments.json"
        embedding_key = f"embeddings/{video_id}_embeddings.json"

        artifact_write_start = perf_counter()
        try:
            storage.write_text(transcript_key, transcript_text)
            storage.write_json(segments_key, all_segments, indent=4)
            storage.write_json(embedding_key, all_embeddings)
        except Exception as exc:
            raise_classified(
                exc,
                message="Could not upload final search artifacts to S3",
                error_code="final_search_artifact_upload_failed",
            )
        final_artifact_write_seconds = seconds_since(artifact_write_start)

        video = update_video(db, video_id, {
            "transcript_path": transcript_key,
            "transcript_status": "completed",
            "segments_path": segments_key,
            "segments_status": "completed",
            "embedding_path": embedding_key,
            "embedding_status": "completed",
            "error_message": None,
        })

        completed_at_epoch = now_epoch()
        time_to_searchable_seconds = seconds_from_upload(storage, video_id)

        try:
            metrics = read_metrics(storage, video_id)
            search_started_at_epoch = metrics.get("processing", {}).get("search_started_at_epoch")
            search_pipeline_seconds = None
            if search_started_at_epoch:
                search_pipeline_seconds = round(completed_at_epoch - search_started_at_epoch, 4)

            chunk_artifact_write_seconds = sum_chunk_metric(chunks, "s3_artifact_write_seconds")
            if chunk_artifact_write_seconds is not None:
                s3_artifact_write_seconds = round(
                    chunk_artifact_write_seconds + final_artifact_write_seconds,
                    4,
                )
            else:
                s3_artifact_write_seconds = final_artifact_write_seconds

            safe_update_metrics(storage, video_id, "processing", {
                "search_completed_at_epoch": completed_at_epoch,
                "time_to_searchable_seconds": time_to_searchable_seconds,
                "search_pipeline_seconds": search_pipeline_seconds,
                "search_completed_chunks": counts["completed"],
                "search_failed_chunks": counts["failed"],
                "search_chunk_count": counts["total"],
                "search_chunk_processing_seconds": sum_chunk_metric(chunks, "processing_seconds"),
                "s3_audio_download_seconds": sum_chunk_metric(chunks, "s3_audio_download_seconds"),
                "whisper_model_load_or_get_seconds": sum_chunk_metric(chunks, "whisper_model_load_or_get_seconds"),
                "whisper_transcription_seconds": sum_chunk_metric(chunks, "whisper_transcription_seconds"),
                "embedding_model_load_or_get_seconds": sum_chunk_metric(chunks, "embedding_model_load_or_get_seconds"),
                "embedding_generation_seconds": sum_chunk_metric(chunks, "embedding_generation_seconds"),
                "qdrant_upsert_seconds": sum_chunk_metric(chunks, "qdrant_upsert_seconds"),
                "final_search_artifact_write_seconds": final_artifact_write_seconds,
                "s3_artifact_write_seconds": s3_artifact_write_seconds,
                "search_finalize_seconds": seconds_since(finalize_start),
                "segment_count": len(all_segments),
                "transcript_character_count": len(transcript_text),
            })
        except Exception:
            pass

        record_full_processing_if_ready(db, storage, video_id)
        return video
    finally:
        db.close()


def process_video_search_artifacts(video_id):
    db = create_session()
    storage = get_storage()
    search_start = perf_counter()

    try:
        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        if video.embedding_status == "completed":
            record_full_processing_if_ready(db, storage, video_id)
            return

        update_video(db, video_id, {
            "status": "processing",
            "audio_status": "processing",
            "transcript_status": "processing",
            "segments_status": "processing",
            "embedding_status": "processing",
            "error_message": None,
        })

        try:
            safe_update_metrics(storage, video_id, "processing", {
                "search_started_at_epoch": now_epoch(),
                "search_queue_wait_seconds": seconds_from_upload(storage, video_id),
            })
        except Exception:
            pass

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            filename = video.filename
            raw_key = video.raw_path
            raw_path = temp_dir / filename
            audio_path = temp_dir / f"{video_id}.wav"

            raw_download_start = perf_counter()
            try:
                storage.download_file(raw_key, raw_path)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not download raw video from S3",
                    error_code="raw_video_download_failed",
                )
            search_raw_download_seconds = seconds_since(raw_download_start)

            try:
                ffmpeg_path = get_ffmpeg_path()
            except Exception as exc:
                raise PermanentProcessingError(
                    "FFmpeg is not available in the worker",
                    error_code="ffmpeg_not_available",
                    cause=exc,
                ) from exc

            audio_extraction_start = perf_counter()
            try:
                subprocess.run(
                    [
                        ffmpeg_path,
                        "-y",
                        "-i", str(raw_path),
                        "-vn",
                        "-acodec", "pcm_s16le",
                        "-ar", "16000",
                        "-ac", "1",
                        str(audio_path),
                    ],
                    check=True,
                )
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Audio extraction failed",
                    error_code="audio_extraction_failed",
                )
            audio_extraction_seconds = seconds_since(audio_extraction_start)

            update_video(db, video_id, {
                "audio_status": "extracted",
            })

            whisper_model_start = perf_counter()
            try:
                whisper_model = get_whisper_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper model could not be loaded",
                    error_code="whisper_model_load_failed",
                )
            whisper_model_load_or_get_seconds = seconds_since(whisper_model_start)

            whisper_start = perf_counter()
            try:
                result = whisper_model.transcribe(str(audio_path))
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper transcription failed",
                    error_code="whisper_transcription_failed",
                )
            whisper_transcription_seconds = seconds_since(whisper_start)

            transcript_text = result["text"]
            segments = result["segments"]

            embedding_model_start = perf_counter()
            try:
                get_embedding_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding model could not be loaded",
                    error_code="embedding_model_load_failed",
                )
            embedding_model_load_or_get_seconds = seconds_since(embedding_model_start)

            embedding_start = perf_counter()
            try:
                segment_embeddings = build_segment_embeddings(segments)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding generation failed",
                    error_code="embedding_generation_failed",
                )
            embedding_generation_seconds = seconds_since(embedding_start)

            transcript_key = f"transcripts/{video_id}.txt"
            segments_key = f"transcripts/{video_id}_segments.json"
            embedding_key = f"embeddings/{video_id}_embeddings.json"
            audio_key = f"audio/{video_id}.wav"

            qdrant_start = perf_counter()
            try:
                delete_segments(video_id)
                upsert_segments(video_id, segment_embeddings)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Qdrant vector replacement failed",
                    error_code="qdrant_vector_replacement_failed",
                )
            qdrant_upsert_seconds = seconds_since(qdrant_start)

            artifact_write_start = perf_counter()

            audio_upload_start = perf_counter()
            try:
                storage.upload_file(audio_path, audio_key)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload extracted audio to S3",
                    error_code="audio_upload_failed",
                )
            s3_audio_upload_seconds = seconds_since(audio_upload_start)

            embedding_write_start = perf_counter()
            try:
                storage.write_json(embedding_key, segment_embeddings)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload embeddings artifact to S3",
                    error_code="embedding_artifact_upload_failed",
                )
            s3_embedding_write_seconds = seconds_since(embedding_write_start)

            transcript_write_start = perf_counter()
            try:
                storage.write_text(transcript_key, transcript_text)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload transcript artifact to S3",
                    error_code="transcript_artifact_upload_failed",
                )
            s3_transcript_write_seconds = seconds_since(transcript_write_start)

            segments_write_start = perf_counter()
            try:
                storage.write_json(segments_key, segments, indent=4)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload segment artifact to S3",
                    error_code="segments_artifact_upload_failed",
                )
            s3_segments_write_seconds = seconds_since(segments_write_start)
            search_artifact_write_seconds = seconds_since(artifact_write_start)

            update_video(db, video_id, {
                "audio_path": audio_key,
                "audio_status": "extracted",
                "transcript_path": transcript_key,
                "transcript_status": "completed",
                "segments_path": segments_key,
                "segments_status": "completed",
                "embedding_path": embedding_key,
                "embedding_status": "completed",
            })

            time_to_searchable_seconds = seconds_from_upload(storage, video_id)

            try:
                safe_update_metrics(storage, video_id, "processing", {
                    "search_completed_at_epoch": now_epoch(),
                    "time_to_searchable_seconds": time_to_searchable_seconds,
                    "search_pipeline_seconds": seconds_since(search_start),
                    "search_raw_download_seconds": search_raw_download_seconds,
                    "s3_raw_download_seconds": search_raw_download_seconds,
                    "audio_extraction_seconds": audio_extraction_seconds,
                    "whisper_model_load_or_get_seconds": whisper_model_load_or_get_seconds,
                    "whisper_transcription_seconds": whisper_transcription_seconds,
                    "embedding_model_load_or_get_seconds": embedding_model_load_or_get_seconds,
                    "embedding_generation_seconds": embedding_generation_seconds,
                    "qdrant_upsert_seconds": qdrant_upsert_seconds,
                    "s3_audio_upload_seconds": s3_audio_upload_seconds,
                    "s3_embedding_write_seconds": s3_embedding_write_seconds,
                    "s3_transcript_write_seconds": s3_transcript_write_seconds,
                    "s3_segments_write_seconds": s3_segments_write_seconds,
                    "search_artifact_write_seconds": search_artifact_write_seconds,
                    "segment_count": len(segments),
                    "transcript_character_count": len(transcript_text),
                })
            except Exception:
                pass

            record_full_processing_if_ready(db, storage, video_id)
    finally:
        db.close()


def process_video_playback_artifacts(video_id):
    db = create_session()
    storage = get_storage()
    playback_start = perf_counter()

    try:
        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        if video.processed_path:
            record_full_processing_if_ready(db, storage, video_id)
            return

        update_video(db, video_id, {
            "status": "processing",
            "error_message": None,
        })

        try:
            safe_update_metrics(storage, video_id, "processing", {
                "playback_started_at_epoch": now_epoch(),
                "playback_queue_wait_seconds": seconds_from_upload(storage, video_id),
            })
        except Exception:
            pass

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            filename = video.filename
            raw_key = video.raw_path
            raw_path = temp_dir / filename
            processed_filename = f"processed_{filename}"
            processed_path = temp_dir / processed_filename

            raw_download_start = perf_counter()
            try:
                storage.download_file(raw_key, raw_path)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not download raw video from S3",
                    error_code="raw_video_download_failed",
                )
            playback_raw_download_seconds = seconds_since(raw_download_start)

            try:
                ffmpeg_path = get_ffmpeg_path()
            except Exception as exc:
                raise PermanentProcessingError(
                    "FFmpeg is not available in the worker",
                    error_code="ffmpeg_not_available",
                    cause=exc,
                ) from exc

            ffmpeg_preset = os.getenv("FFMPEG_PRESET", "veryfast")

            transcode_start = perf_counter()
            try:
                subprocess.run(
                    [
                        ffmpeg_path,
                        "-y",
                        "-i", str(raw_path),
                        "-vf", "scale=-2:360",
                        "-preset", ffmpeg_preset,
                        str(processed_path),
                    ],
                    check=True,
                )
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Video transcode failed",
                    error_code="video_transcode_failed",
                )
            video_transcode_seconds = seconds_since(transcode_start)

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

            processed_key = f"processed_videos/{processed_filename}"
            thumbnail_key = f"thumbnails/{video_id}.jpg"

            processed_upload_start = perf_counter()
            try:
                storage.upload_file(processed_path, processed_key)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload processed video to S3",
                    error_code="processed_video_upload_failed",
                )
            s3_processed_upload_seconds = seconds_since(processed_upload_start)

            s3_thumbnail_upload_seconds = None
            if thumbnail_path and thumbnail_path.exists():
                thumbnail_upload_start = perf_counter()
                try:
                    storage.upload_file(thumbnail_path, thumbnail_key)
                except Exception as exc:
                    raise_classified(
                        exc,
                        message="Could not upload thumbnail to S3",
                        error_code="thumbnail_upload_failed",
                    )
                s3_thumbnail_upload_seconds = seconds_since(thumbnail_upload_start)

            update_video(db, video_id, {
                "processed_path": processed_key,
            })

            try:
                safe_update_metrics(storage, video_id, "processing", {
                    "playback_completed_at_epoch": now_epoch(),
                    "ffmpeg_preset": ffmpeg_preset,
                    "playback_pipeline_seconds": seconds_since(playback_start),
                    "playback_raw_download_seconds": playback_raw_download_seconds,
                    "video_transcode_seconds": video_transcode_seconds,
                    "thumbnail_generation_seconds": thumbnail_generation_seconds,
                    "s3_processed_upload_seconds": s3_processed_upload_seconds,
                    "s3_thumbnail_upload_seconds": s3_thumbnail_upload_seconds,
                })
            except Exception:
                pass

            record_full_processing_if_ready(db, storage, video_id)
    finally:
        db.close()


def process_video(video_id, processing_token: str):
    db = create_session()
    storage = get_storage()
    processing_start = perf_counter()
    processing_started_at_epoch = now_epoch()

    try:
        video = get_video(db, video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video not found",
                error_code="video_not_found",
            )

        ensure_current_attempt(db, video_id, processing_token)

        try:
            metrics = read_metrics(storage, video_id)
            accepted_at_epoch = metrics.get("upload", {}).get("accepted_at_epoch")
            queue_wait_seconds = None
            if accepted_at_epoch:
                queue_wait_seconds = round(processing_started_at_epoch - accepted_at_epoch, 4)

            safe_update_metrics(storage, video_id, "processing", {
                "started_at_epoch": processing_started_at_epoch,
                "queue_wait_seconds": queue_wait_seconds,
            })
        except Exception:
            pass

        with TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            filename = video.filename
            raw_key = video.raw_path

            raw_path = temp_dir / filename
            processed_filename = f"processed_{filename}"
            processed_path = temp_dir / processed_filename
            audio_path = temp_dir / f"{video_id}.wav"

            raw_download_start = perf_counter()
            try:
                storage.download_file(raw_key, raw_path)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not download raw video from S3",
                    error_code="raw_video_download_failed",
                )
            s3_raw_download_seconds = seconds_since(raw_download_start)

            try:
                ffmpeg_path = get_ffmpeg_path()
            except Exception as exc:
                raise PermanentProcessingError(
                    "FFmpeg is not available in the worker",
                    error_code="ffmpeg_not_available",
                    cause=exc,
                ) from exc
            ffmpeg_preset = os.getenv("FFMPEG_PRESET", "veryfast")
            
            transcode_start = perf_counter()
            try:
                subprocess.run(
                    [
                        ffmpeg_path,
                        "-y",
                        "-i", str(raw_path),
                        "-vf", "scale=-2:360",
                        "-preset", ffmpeg_preset,
                        str(processed_path),
                    ],
                    check=True,
                )
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Video transcode failed",
                    error_code="video_transcode_failed",
                )
            video_transcode_seconds = seconds_since(transcode_start)

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

            audio_extraction_start = perf_counter()
            try:
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
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Audio extraction failed",
                    error_code="audio_extraction_failed",
                )
            audio_extraction_seconds = seconds_since(audio_extraction_start)

            update_attempt(db, video_id, processing_token, {
                "audio_status": "extracted",
                "transcript_status": "processing",
                "segments_status": "processing",
                "embedding_status": "processing",
            })
            
            # Whisper part 
            whisper_model_start = perf_counter()
            try:
                whisper_model = get_whisper_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper model could not be loaded",
                    error_code="whisper_model_load_failed",
                )
            whisper_model_load_or_get_seconds = seconds_since(whisper_model_start)

            whisper_start = perf_counter()
            try:
                result = whisper_model.transcribe(str(audio_path))
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Whisper transcription failed",
                    error_code="whisper_transcription_failed",
                )
            whisper_transcription_seconds = seconds_since(whisper_start)


            transcript_text = result["text"]
            segments = result["segments"]

            # Emebedding time
            embedding_model_start = perf_counter()
            try:
                get_embedding_model()
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding model could not be loaded",
                    error_code="embedding_model_load_failed",
                )
            embedding_model_load_or_get_seconds = seconds_since(embedding_model_start)

            embedding_start = perf_counter()
            try:
                segment_embeddings = build_segment_embeddings(segments)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Embedding generation failed",
                    error_code="embedding_generation_failed",
                )
            embedding_generation_seconds = seconds_since(embedding_start)  


            processed_key = f"processed_videos/{processed_filename}"
            audio_key = f"audio/{video_id}.wav"
            thumbnail_key = f"thumbnails/{video_id}.jpg"
            transcript_key = f"transcripts/{video_id}.txt"
            segments_key = f"transcripts/{video_id}_segments.json"
            embedding_key = f"embeddings/{video_id}_embeddings.json"

            qdrant_start = perf_counter()
            ensure_current_attempt(db, video_id, processing_token)
            try:
                delete_segments(video_id)
                upsert_segments(video_id, segment_embeddings)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Qdrant vector replacement failed",
                    error_code="qdrant_vector_replacement_failed",
                )
            qdrant_upsert_seconds = seconds_since(qdrant_start)

            update_attempt(db, video_id, processing_token, {
                "embedding_status": "completed",
            })

            artifact_write_start = perf_counter()

            processed_upload_start = perf_counter()
            try:
                storage.upload_file(processed_path, processed_key)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload processed video to S3",
                    error_code="processed_video_upload_failed",
                )
            s3_processed_upload_seconds = seconds_since(processed_upload_start)

            audio_upload_start = perf_counter()
            try:
                storage.upload_file(audio_path, audio_key)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload extracted audio to S3",
                    error_code="audio_upload_failed",
                )
            s3_audio_upload_seconds = seconds_since(audio_upload_start)

            s3_thumbnail_upload_seconds = None
            if thumbnail_path and thumbnail_path.exists():
                thumbnail_upload_start = perf_counter()
                try:
                    storage.upload_file(thumbnail_path, thumbnail_key)
                except Exception as exc:
                    raise_classified(
                        exc,
                        message="Could not upload thumbnail to S3",
                        error_code="thumbnail_upload_failed",
                    )
                s3_thumbnail_upload_seconds = seconds_since(thumbnail_upload_start)

            embedding_write_start = perf_counter()
            try:
                storage.write_json(embedding_key, segment_embeddings)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload embeddings artifact to S3",
                    error_code="embedding_artifact_upload_failed",
                )
            s3_embedding_write_seconds = seconds_since(embedding_write_start)

            transcript_write_start = perf_counter()
            try:
                storage.write_text(transcript_key, transcript_text)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload transcript artifact to S3",
                    error_code="transcript_artifact_upload_failed",
                )
            s3_transcript_write_seconds = seconds_since(transcript_write_start)

            segments_write_start = perf_counter()
            try:
                storage.write_json(segments_key, segments, indent=4)
            except Exception as exc:
                raise_classified(
                    exc,
                    message="Could not upload segment artifact to S3",
                    error_code="segments_artifact_upload_failed",
                )
            s3_segments_write_seconds = seconds_since(segments_write_start)
            s3_artifact_write_seconds = seconds_since(artifact_write_start)

            final_video = record_processing_success(db, video_id, processing_token, {
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
            if final_video is None:
                raise StaleProcessingAttempt(
                    "Processing attempt no longer owns this video",
                    error_code="stale_processing_attempt",
                )
            
            try:
                metrics = read_metrics(storage, video_id)
                accepted_at_epoch = metrics.get("upload", {}).get("accepted_at_epoch")

                time_to_searchable_seconds = None
                if accepted_at_epoch:
                    time_to_searchable_seconds = round(now_epoch() - accepted_at_epoch, 4)

                safe_update_metrics(storage, video_id, "processing", {
                    "completed_at_epoch": now_epoch(),
                    "time_to_searchable_seconds": time_to_searchable_seconds,
                    "ffmpeg_preset": ffmpeg_preset,
                    "s3_raw_download_seconds": s3_raw_download_seconds,
                    "video_transcode_seconds": video_transcode_seconds,
                    "audio_extraction_seconds": audio_extraction_seconds,
                    "whisper_model_load_or_get_seconds": whisper_model_load_or_get_seconds,
                    "whisper_transcription_seconds": whisper_transcription_seconds,
                    "embedding_model_load_or_get_seconds": embedding_model_load_or_get_seconds,
                    "embedding_generation_seconds": embedding_generation_seconds,
                    "qdrant_upsert_seconds": qdrant_upsert_seconds,
                    "thumbnail_generation_seconds": thumbnail_generation_seconds,
                    "s3_processed_upload_seconds": s3_processed_upload_seconds,
                    "s3_audio_upload_seconds": s3_audio_upload_seconds,
                    "s3_thumbnail_upload_seconds": s3_thumbnail_upload_seconds,
                    "s3_embedding_write_seconds": s3_embedding_write_seconds,
                    "s3_transcript_write_seconds": s3_transcript_write_seconds,
                    "s3_segments_write_seconds": s3_segments_write_seconds,
                    "s3_artifact_write_seconds": s3_artifact_write_seconds,
                    "segment_count": len(segments),
                    "transcript_character_count": len(transcript_text),
                    "total_processing_seconds": seconds_since(processing_start),
                })
            except Exception:
                pass
    finally:
        db.close()
