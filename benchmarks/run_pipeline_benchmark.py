from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError as exc:
    raise SystemExit("Install project dependencies first: requests is required.") from exc

from benchmark_common import (
    BENCHMARK_DIR,
    append_csv_row,
    nested_get,
    read_csv_rows,
    resolve_repo_path,
    safe_float,
    video_metadata,
)


VIDEOS_CSV = BENCHMARK_DIR / "videos.csv"
PIPELINE_CSV = BENCHMARK_DIR / "baseline_current_pipeline.csv"
VIDEO_IDS_CSV = BENCHMARK_DIR / "current_video_ids.csv"

PIPELINE_FIELDNAMES = [
    "run_id",
    "video_key",
    "video_name",
    "file_path",
    "file_size_bytes",
    "file_size_mb",
    "duration_seconds",
    "api_base_url",
    "video_id",
    "status",
    "error_message",
    "upload_request_elapsed_seconds",
    "upload_response_latency_seconds",
    "s3_raw_upload_seconds",
    "celery_enqueue_seconds",
    "queue_wait_seconds",
    "time_to_searchable_seconds",
    "total_processing_seconds",
    "s3_raw_download_seconds",
    "video_transcode_seconds",
    "audio_extraction_seconds",
    "whisper_model_load_or_get_seconds",
    "whisper_transcription_seconds",
    "embedding_model_load_or_get_seconds",
    "embedding_generation_seconds",
    "qdrant_upsert_seconds",
    "thumbnail_generation_seconds",
    "s3_artifact_write_seconds",
    "search_architecture",
    "search_chunk_count",
    "search_chunk_seconds",
    "search_chunk_overlap_seconds",
    "search_chunk_preparation_seconds",
    "search_chunk_processing_seconds",
    "search_finalize_seconds",
    "s3_audio_download_seconds",
    "final_search_artifact_write_seconds",
    "segment_count",
    "transcript_character_count",
    "completed_at",
]

VIDEO_ID_FIELDNAMES = [
    "run_id",
    "video_key",
    "video_id",
    "title",
    "status",
    "created_at",
]


def build_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def resolve_token(args: argparse.Namespace) -> str | None:
    if args.token:
        return args.token
    if args.token_env:
        return os.getenv(args.token_env)
    if args.token_file:
        token_path = Path(args.token_file)
        return token_path.read_text(encoding="utf-8").strip()
    return None


def api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def request_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.ok:
        return payload

    detail = payload.get("detail") if isinstance(payload, dict) else None
    message = detail if isinstance(detail, str) else response.text
    raise RuntimeError(f"HTTP {response.status_code}: {message}")


def upload_video(
    *,
    base_url: str,
    headers: dict[str, str],
    video_path: Path,
    title: str,
    visibility: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    with video_path.open("rb") as file:
        response = requests.post(
            api_url(base_url, "/videos/upload"),
            headers=headers,
            data={"title": title, "visibility": visibility},
            files={"file": (video_path.name, file, "video/mp4")},
            timeout=timeout_seconds,
        )
    elapsed = round(time.perf_counter() - start, 4)
    return request_json(response), elapsed


def get_status(base_url: str, headers: dict[str, str], video_id: str) -> dict[str, Any]:
    response = requests.get(
        api_url(base_url, f"/videos/{video_id}/status"),
        headers=headers,
        timeout=30,
    )
    return request_json(response)


def get_metrics(base_url: str, headers: dict[str, str], video_id: str) -> dict[str, Any]:
    response = requests.get(
        api_url(base_url, f"/videos/{video_id}/metrics"),
        headers=headers,
        timeout=30,
    )
    return request_json(response)


def wait_for_terminal_status(
    *,
    base_url: str,
    headers: dict[str, str],
    video_id: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] = {}

    while time.monotonic() < deadline:
        last_status = get_status(base_url, headers, video_id)
        status = str(last_status.get("status", "")).lower()
        if status in {"processed", "failed"}:
            return last_status
        time.sleep(poll_seconds)

    return {
        **last_status,
        "status": "timeout",
        "error_message": f"Timed out after {timeout_seconds} seconds",
    }


def flatten_result(
    *,
    run_id: str,
    video_row: dict[str, str],
    video_path: Path,
    metadata: dict[str, Any],
    base_url: str,
    upload_elapsed: float,
    upload_payload: dict[str, Any],
    final_status: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    processing = metrics.get("processing", {}) if isinstance(metrics, dict) else {}
    upload = metrics.get("upload", {}) if isinstance(metrics, dict) else {}

    return {
        "run_id": run_id,
        "video_key": video_row["video_key"],
        "video_name": video_path.name,
        "file_path": video_row["file_path"],
        "file_size_bytes": metadata.get("file_size_bytes", ""),
        "file_size_mb": metadata.get("file_size_mb", ""),
        "duration_seconds": metadata.get("duration_seconds", ""),
        "api_base_url": base_url,
        "video_id": upload_payload.get("video_id", ""),
        "status": final_status.get("status", upload_payload.get("status", "")),
        "error_message": final_status.get("error_message", ""),
        "upload_request_elapsed_seconds": upload_elapsed,
        "upload_response_latency_seconds": upload.get("upload_response_latency_seconds", ""),
        "s3_raw_upload_seconds": upload.get("s3_raw_upload_seconds", ""),
        "celery_enqueue_seconds": upload.get("celery_enqueue_seconds", ""),
        "queue_wait_seconds": processing.get("queue_wait_seconds", ""),
        "time_to_searchable_seconds": processing.get("time_to_searchable_seconds", ""),
        "total_processing_seconds": processing.get("total_processing_seconds", ""),
        "s3_raw_download_seconds": processing.get("s3_raw_download_seconds", ""),
        "video_transcode_seconds": processing.get("video_transcode_seconds", ""),
        "audio_extraction_seconds": processing.get("audio_extraction_seconds", ""),
        "whisper_model_load_or_get_seconds": processing.get("whisper_model_load_or_get_seconds", ""),
        "whisper_transcription_seconds": processing.get("whisper_transcription_seconds", ""),
        "embedding_model_load_or_get_seconds": processing.get("embedding_model_load_or_get_seconds", ""),
        "embedding_generation_seconds": processing.get("embedding_generation_seconds", ""),
        "qdrant_upsert_seconds": processing.get("qdrant_upsert_seconds", ""),
        "thumbnail_generation_seconds": processing.get("thumbnail_generation_seconds", ""),
        "s3_artifact_write_seconds": processing.get("s3_artifact_write_seconds", ""),
        "search_architecture": processing.get("search_architecture", ""),
        "search_chunk_count": processing.get("search_chunk_count", ""),
        "search_chunk_seconds": processing.get("search_chunk_seconds", ""),
        "search_chunk_overlap_seconds": processing.get("search_chunk_overlap_seconds", ""),
        "search_chunk_preparation_seconds": processing.get("search_chunk_preparation_seconds", ""),
        "search_chunk_processing_seconds": processing.get("search_chunk_processing_seconds", ""),
        "search_finalize_seconds": processing.get("search_finalize_seconds", ""),
        "s3_audio_download_seconds": processing.get("s3_audio_download_seconds", ""),
        "final_search_artifact_write_seconds": processing.get("final_search_artifact_write_seconds", ""),
        "segment_count": processing.get("segment_count", ""),
        "transcript_character_count": processing.get("transcript_character_count", ""),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def run_once(args: argparse.Namespace, video_row: dict[str, str], run_number: int) -> None:
    video_path = resolve_repo_path(video_row["file_path"])
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    metadata = video_metadata(video_path)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{video_row['video_key']}-{run_number}"
    title = f"benchmark-{video_row['video_key']}-{run_id}"
    headers = build_headers(resolve_token(args))

    print(f"Uploading {video_path.name} as {title}")
    upload_payload, upload_elapsed = upload_video(
        base_url=args.api_base_url,
        headers=headers,
        video_path=video_path,
        title=title,
        visibility=args.visibility,
        timeout_seconds=args.upload_timeout_seconds,
    )

    video_id = upload_payload["video_id"]
    print(f"Uploaded {video_path.name}: video_id={video_id}")

    final_status = wait_for_terminal_status(
        base_url=args.api_base_url,
        headers=headers,
        video_id=video_id,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.processing_timeout_seconds,
    )
    print(f"Final status for {video_id}: {final_status.get('status')}")

    metrics: dict[str, Any] = {}
    try:
        metrics = get_metrics(args.api_base_url, headers, video_id)
    except Exception as exc:
        metrics = {"metrics_error": str(exc)}

    append_csv_row(
        args.pipeline_output,
        PIPELINE_FIELDNAMES,
        flatten_result(
            run_id=run_id,
            video_row=video_row,
            video_path=video_path,
            metadata=metadata,
            base_url=args.api_base_url,
            upload_elapsed=upload_elapsed,
            upload_payload=upload_payload,
            final_status=final_status,
            metrics=metrics,
        ),
    )

    append_csv_row(
        args.video_ids_output,
        VIDEO_ID_FIELDNAMES,
        {
            "run_id": run_id,
            "video_key": video_row["video_key"],
            "video_id": video_id,
            "title": title,
            "status": final_status.get("status", ""),
            "created_at": nested_get(final_status, "created_at", ""),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Vindex current-pipeline benchmark.")
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--token", default=None, help="Bearer token when auth is enabled.")
    parser.add_argument("--token-env", default=None, help="Environment variable containing the bearer token.")
    parser.add_argument("--token-file", default=None, help="File containing the bearer token.")
    parser.add_argument("--videos-csv", type=Path, default=VIDEOS_CSV)
    parser.add_argument("--pipeline-output", type=Path, default=PIPELINE_CSV)
    parser.add_argument("--video-ids-output", type=Path, default=VIDEO_IDS_CSV)
    parser.add_argument("--visibility", default="private", choices=["private", "public"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--poll-seconds", type=int, default=4)
    parser.add_argument("--upload-timeout-seconds", type=int, default=1800)
    parser.add_argument("--processing-timeout-seconds", type=int, default=7200)
    parser.add_argument("--only", default=None, help="Optional comma-separated video_key filter.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    videos = read_csv_rows(args.videos_csv)
    if args.only:
        requested = {value.strip() for value in args.only.split(",") if value.strip()}
        videos = [video for video in videos if video["video_key"] in requested]

    for run_number in range(1, args.runs + 1):
        for video_row in videos:
            run_once(args, video_row, run_number)

    print(f"Wrote {args.pipeline_output}")
    print(f"Wrote {args.video_ids_output}")


if __name__ == "__main__":
    main()
