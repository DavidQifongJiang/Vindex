from __future__ import annotations

import argparse
import json
import os
import sys
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
    percentile,
    read_csv_rows,
    safe_float,
    write_csv_rows,
    write_json,
)


LABELS_CSV = BENCHMARK_DIR / "semantic_search_labels.csv"
VIDEO_IDS_CSV = BENCHMARK_DIR / "current_video_ids.csv"
RESULTS_CSV = BENCHMARK_DIR / "baseline_search_quality.csv"
SUMMARY_JSON = BENCHMARK_DIR / "baseline_search_quality_summary.json"
RAW_RESULTS_JSON = BENCHMARK_DIR / "baseline_search_quality_raw.json"
CONFIG_JSON = BENCHMARK_DIR / "baseline_search_quality_config.json"
REPORT_MD = BENCHMARK_DIR / "baseline_search_quality_report.md"
TOP_K = 5

RESULT_FIELDNAMES = [
    "label_id",
    "video_key",
    "video_id",
    "query",
    "query_type",
    "expected_start_seconds",
    "expected_end_seconds",
    "relevance_window_seconds",
    "expected_text",
    "notes",
    "result_count",
    "hit_at_1",
    "hit_at_5",
    "precision_at_5",
    "reciprocal_rank",
    "first_relevant_rank",
    "timestamp_error_seconds",
    "top_result_start",
    "top_result_end",
    "top_result_score",
    "top_result_text",
    "first_relevant_start",
    "first_relevant_end",
    "first_relevant_score",
    "first_relevant_text",
    "request_latency_seconds",
    "api_search_latency_seconds",
    "api_query_embedding_seconds",
    "api_qdrant_search_seconds",
    "failure_category",
    "failure_reason",
]


def build_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_token(args: argparse.Namespace) -> str | None:
    if args.token:
        return args.token
    if args.token_env:
        return os.getenv(args.token_env)
    if args.token_file:
        return args.token_file.read_text(encoding="utf-8").strip()
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


def latest_processed_video_ids(rows: list[dict[str, str]]) -> dict[str, str]:
    video_ids: dict[str, str] = {}
    for row in rows:
        if row.get("video_id") and row.get("status") == "processed":
            video_ids[row["video_key"]] = row["video_id"]
    return video_ids


def read_env_setting(name: str, default: str) -> str:
    if os.getenv(name):
        return str(os.getenv(name))

    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("'\"") or default

    return default


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def is_relevant(
    result: dict[str, Any],
    expected_start: float,
    expected_end: float,
    relevance_window: float,
) -> bool:
    result_start = safe_float(result.get("start"))
    result_end = safe_float(result.get("end"))
    if result_start is None:
        return False

    if result_end is not None and result_start <= expected_end and result_end >= expected_start:
        return True

    return abs(result_start - expected_start) <= relevance_window


def get_json(base_url: str, headers: dict[str, str], path: str) -> dict[str, Any]:
    response = requests.get(
        api_url(base_url, path),
        headers=headers,
        timeout=30,
    )
    return request_json(response)


def search_video(
    *,
    base_url: str,
    headers: dict[str, str],
    video_id: str,
    query: str,
    algorithm: str,
) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    response = requests.post(
        api_url(base_url, f"/videos/{video_id}/search"),
        headers=headers,
        data=json.dumps({"query": query, "algorithm": algorithm}),
        timeout=60,
    )
    elapsed = round(time.perf_counter() - start, 4)
    return request_json(response), elapsed


def failure_details(row: dict[str, Any]) -> tuple[str, str]:
    hit_at_5 = safe_float(row.get("hit_at_5")) or 0.0
    hit_at_1 = safe_float(row.get("hit_at_1")) or 0.0
    top_score = safe_float(row.get("top_result_score"))

    if hit_at_1:
        return "", ""
    if hit_at_5:
        return "low_rank", "A relevant segment was retrieved, but not at rank 1."
    if safe_float(row.get("result_count")) == 0:
        return "no_results", "The API returned no top-5 results for this query."
    if top_score is not None and top_score >= 0.45:
        return "topic_confusion", "Top result had a plausible semantic score but pointed to a different timestamp."
    return "embedding_mismatch", "No relevant result appeared in the top 5."


def evaluate_label(
    *,
    label: dict[str, str],
    video_id: str,
    results: list[dict[str, Any]],
    relevant_flags: list[bool],
    request_latency: float,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    expected_start = safe_float(label.get("expected_start_seconds"))
    expected_end = safe_float(label.get("expected_end_seconds"))
    relevance_window = safe_float(label.get("relevance_window_seconds")) or 30.0

    if expected_start is None or expected_end is None:
        raise ValueError(f"Label {label.get('label_id')} is missing expected timestamps.")

    relevant_count = sum(1 for value in relevant_flags if value)
    hit_at_5 = 1 if relevant_count > 0 else 0
    hit_at_1 = 1 if relevant_flags[:1] == [True] else 0
    precision_at_5 = relevant_count / TOP_K

    first_relevant_rank = ""
    first_relevant: dict[str, Any] = {}
    timestamp_error = ""
    reciprocal_rank = 0.0
    for index, is_match in enumerate(relevant_flags, start=1):
        if is_match:
            first_relevant_rank = index
            reciprocal_rank = 1 / index
            first_relevant = results[index - 1]
            result_start = safe_float(results[index - 1].get("start"))
            if result_start is not None:
                timestamp_error = round(abs(result_start - expected_start), 4)
            break

    top_result = results[0] if results else {}
    row = {
        "label_id": label.get("label_id", ""),
        "video_key": label["video_key"],
        "video_id": video_id,
        "query": label["query"],
        "query_type": label.get("query_type", ""),
        "expected_start_seconds": expected_start,
        "expected_end_seconds": expected_end,
        "relevance_window_seconds": relevance_window,
        "expected_text": label.get("expected_text", ""),
        "notes": label.get("notes", ""),
        "result_count": len(results),
        "hit_at_1": hit_at_1,
        "hit_at_5": hit_at_5,
        "precision_at_5": round(precision_at_5, 4),
        "reciprocal_rank": round(reciprocal_rank, 4),
        "first_relevant_rank": first_relevant_rank,
        "timestamp_error_seconds": timestamp_error,
        "top_result_start": top_result.get("start", ""),
        "top_result_end": top_result.get("end", ""),
        "top_result_score": top_result.get("score", ""),
        "top_result_text": top_result.get("text", ""),
        "first_relevant_start": first_relevant.get("start", ""),
        "first_relevant_end": first_relevant.get("end", ""),
        "first_relevant_score": first_relevant.get("score", ""),
        "first_relevant_text": first_relevant.get("text", ""),
        "request_latency_seconds": request_latency,
        "api_search_latency_seconds": metrics.get("search_latency_seconds", ""),
        "api_query_embedding_seconds": metrics.get("query_embedding_seconds", ""),
        "api_qdrant_search_seconds": metrics.get("qdrant_search_seconds", ""),
    }
    category, reason = failure_details(row)
    row["failure_category"] = category
    row["failure_reason"] = reason
    return row


def raw_result_entry(
    *,
    label: dict[str, str],
    video_id: str,
    results: list[dict[str, Any]],
    relevant_flags: list[bool],
    request_latency: float,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "label": label,
        "video_id": video_id,
        "request_latency_seconds": request_latency,
        "api_metrics": metrics,
        "results": [
            {
                "rank": index,
                "is_relevant": relevant_flags[index - 1],
                **result,
            }
            for index, result in enumerate(results, start=1)
        ],
    }


def evaluate_label_with_raw(
    *,
    args: argparse.Namespace,
    headers: dict[str, str],
    label: dict[str, str],
    video_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_start = safe_float(label.get("expected_start_seconds"))
    expected_end = safe_float(label.get("expected_end_seconds"))
    relevance_window = safe_float(label.get("relevance_window_seconds")) or 30.0

    if expected_start is None or expected_end is None:
        raise ValueError(f"Label {label.get('label_id')} is missing expected timestamps.")

    payload, request_latency = search_video(
        base_url=args.api_base_url,
        headers=headers,
        video_id=video_id,
        query=label["query"],
        algorithm=args.algorithm,
    )

    results = payload.get("results", [])[:TOP_K]
    relevant_flags = [
        is_relevant(result, expected_start, expected_end, relevance_window)
        for result in results
    ]
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    row = evaluate_label(
        label=label,
        video_id=video_id,
        results=results,
        relevant_flags=relevant_flags,
        request_latency=request_latency,
        metrics=metrics,
    )

    return row, raw_result_entry(
        label=label,
        video_id=video_id,
        results=results,
        relevant_flags=relevant_flags,
        request_latency=request_latency,
        metrics=metrics,
    )


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {
            "total_labeled_queries": 0,
            "message": "No filled labels were found.",
        }

    hit_at_1_values = [safe_float(row["hit_at_1"]) or 0.0 for row in rows]
    hit_at_5_values = [safe_float(row["hit_at_5"]) or 0.0 for row in rows]
    precision_values = [safe_float(row["precision_at_5"]) or 0.0 for row in rows]
    rr_values = [safe_float(row["reciprocal_rank"]) or 0.0 for row in rows]
    timestamp_errors = [
        value for value in (safe_float(row["timestamp_error_seconds"]) for row in rows)
        if value is not None
    ]
    request_latencies = [
        value for value in (safe_float(row["request_latency_seconds"]) for row in rows)
        if value is not None
    ]
    api_search_latencies = [
        value for value in (safe_float(row["api_search_latency_seconds"]) for row in rows)
        if value is not None
    ]
    outcome_counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    for row in rows:
        category = row.get("failure_category")
        outcome = category or "hit_at_1"
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        if category:
            failure_counts[category] = failure_counts.get(category, 0) + 1

    return {
        "total_labeled_queries": total,
        "hit_rate_at_1": round(sum(hit_at_1_values) / total, 4),
        "hit_rate_at_5": round(sum(hit_at_5_values) / total, 4),
        "mean_precision_at_5": round(sum(precision_values) / total, 4),
        "mrr": round(sum(rr_values) / total, 4),
        "mean_timestamp_error_seconds": mean(timestamp_errors),
        "median_timestamp_error_seconds": percentile(timestamp_errors, 0.5),
        "p90_timestamp_error_seconds": percentile(timestamp_errors, 0.9),
        "p95_timestamp_error_seconds": percentile(timestamp_errors, 0.95),
        "p50_request_latency_seconds": percentile(request_latencies, 0.5),
        "p95_request_latency_seconds": percentile(request_latencies, 0.95),
        "p50_api_search_latency_seconds": percentile(api_search_latencies, 0.5),
        "p95_api_search_latency_seconds": percentile(api_search_latencies, 0.95),
        "timestamp_error_sample_count": len(timestamp_errors),
        "outcome_counts": outcome_counts,
        "failure_counts": failure_counts,
    }


def build_config(
    *,
    args: argparse.Namespace,
    labels: list[dict[str, str]],
    video_ids: dict[str, str],
    headers: dict[str, str],
) -> dict[str, Any]:
    videos = []
    for video_key, video_id in video_ids.items():
        status: dict[str, Any] = {}
        metrics: dict[str, Any] = {}
        try:
            status = get_json(args.api_base_url, headers, f"/videos/{video_id}/status")
        except Exception as exc:
            status = {"error": str(exc)}
        try:
            metrics = get_json(args.api_base_url, headers, f"/videos/{video_id}/metrics")
        except Exception as exc:
            metrics = {"error": str(exc)}
        videos.append({
            "video_key": video_key,
            "video_id": video_id,
            "title": status.get("title", ""),
            "status": status.get("status", ""),
            "duration_seconds": status.get("duration_seconds", ""),
            "processing": metrics.get("processing", {}) if isinstance(metrics, dict) else {},
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": f"python {' '.join(sys.argv)}",
        "api_base_url": args.api_base_url,
        "auth_mode": "bearer_token" if (args.token or args.token_env or args.token_file) else "dev_user_or_public_api",
        "algorithm": args.algorithm,
        "top_k": TOP_K,
        "score_threshold": "none",
        "embedding_model": read_env_setting("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
        "embedding_vector_size": 384,
        "vector_database": "Qdrant",
        "collection": "vindex_segments",
        "similarity_metric": "cosine",
        "qdrant_limit": TOP_K,
        "transcription_model": read_env_setting("WHISPER_MODEL", "base"),
        "chunk_window_seconds": read_env_setting("SEARCH_CHUNK_SECONDS", "300"),
        "chunk_overlap_seconds": read_env_setting("SEARCH_CHUNK_OVERLAP_SECONDS", "5"),
        "label_source": "manually curated benchmark derived from source transcripts",
        "label_count": len(labels),
        "label_csv": display_path(args.labels_csv),
        "video_ids_csv": display_path(args.video_ids_csv),
        "relevance_rule": (
            "A result is relevant if its timestamp overlaps the expected interval, "
            "or its start time is within relevance_window_seconds of the expected start."
        ),
        "videos": videos,
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def rounded(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    return f"{number:.4f}"


def short_text(value: Any, limit: int = 90) -> str:
    text = str(value or "").replace("|", "\\|").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def write_report(
    path: Path,
    summary: dict[str, Any],
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    artifact_paths: dict[str, Path],
) -> None:
    problem_rows = [
        row for row in rows
        if row.get("failure_category")
    ]
    problem_rows.sort(
        key=lambda row: (
            0 if row.get("failure_category") != "low_rank" else 1,
            -(safe_float(row.get("top_result_score")) or 0.0),
        )
    )

    metric_rows = [
        ["Queries", summary.get("total_labeled_queries", "")],
        ["Hit@1", rounded(summary.get("hit_rate_at_1"))],
        ["Hit@5", rounded(summary.get("hit_rate_at_5"))],
        ["Precision@5", rounded(summary.get("mean_precision_at_5"))],
        ["MRR", rounded(summary.get("mrr"))],
        ["Mean timestamp error, hits only", rounded(summary.get("mean_timestamp_error_seconds"))],
        ["Median timestamp error, hits only", rounded(summary.get("median_timestamp_error_seconds"))],
        ["P90 timestamp error, hits only", rounded(summary.get("p90_timestamp_error_seconds"))],
        ["P95 timestamp error, hits only", rounded(summary.get("p95_timestamp_error_seconds"))],
        ["P50 request latency", rounded(summary.get("p50_request_latency_seconds"))],
        ["P95 request latency", rounded(summary.get("p95_request_latency_seconds"))],
        ["P50 API search latency", rounded(summary.get("p50_api_search_latency_seconds"))],
        ["P95 API search latency", rounded(summary.get("p95_api_search_latency_seconds"))],
    ]
    video_rows = []
    for video in config.get("videos", []):
        processing = video.get("processing", {})
        chunk_count = processing.get("search_chunk_count", "")
        architecture = processing.get("search_architecture", "")
        if not architecture and chunk_count:
            architecture = "chunked"
        video_rows.append([
            video.get("video_key", ""),
            video.get("video_id", ""),
            rounded(processing.get("video_duration_seconds", "")),
            processing.get("segment_count", ""),
            architecture,
            chunk_count,
        ])

    method_notes = [
        "- Labels are manually curated from the source transcripts and have not gone through external reviewer adjudication.",
        "- The benchmark evaluates the current semantic-search implementation only; it does not redesign retrieval, chunk scoring, ranking, or thresholds.",
        "- Precision@5 uses a fixed denominator of 5, even when multiple nearby segments are arguably acceptable to a user.",
        "- Timestamp error excludes top-5 misses, so pair it with Hit@5 instead of reading it alone.",
        "- The local benchmark was run through a temporary dev-user API on port 8001 because the normal local API on port 8000 required Cognito auth.",
    ]
    if "expanded_15min" in config.get("label_csv", ""):
        method_notes.append(
            "- This expanded run uses the first 15 minutes of each long lecture, so it validates lecture-search behavior but not full-video coverage."
        )
    elif "baseline" in display_path(artifact_paths["results"]):
        method_notes.append(
            "- The original short-video baseline includes a 2-minute subset of the same talk, so it validates short-video behavior but is not independent topical coverage."
        )
    outcome_count_rows = [
        [category, count]
        for category, count in sorted(summary.get("outcome_counts", {}).items())
    ]
    failure_count_rows = [
        [category, count]
        for category, count in sorted(summary.get("failure_counts", {}).items())
    ]

    failure_rows = [
        [
            row.get("label_id", ""),
            row.get("video_key", ""),
            short_text(row.get("query", "")),
            f"{row.get('expected_start_seconds', '')}-{row.get('expected_end_seconds', '')}",
            f"{row.get('top_result_start', '')}-{row.get('top_result_end', '')}",
            rounded(row.get("top_result_score")),
            row.get("failure_category", ""),
            short_text(row.get("top_result_text", "")),
        ]
        for row in problem_rows[:10]
    ]

    lines = [
        "# Vindex Semantic Search Quality Benchmark",
        "",
        f"Generated at: `{config['generated_at']}`",
        "",
        "## Frozen Config",
        "",
        markdown_table(
            ["Setting", "Value"],
            [
                ["API base URL", config["api_base_url"]],
                ["Auth mode", config["auth_mode"]],
                ["Algorithm", config["algorithm"]],
                ["Embedding model", config["embedding_model"]],
                ["Vector DB / collection", f"{config['vector_database']} / {config['collection']}"],
                ["Vector size", config["embedding_vector_size"]],
                ["Similarity metric", config["similarity_metric"]],
                ["Top-k", config["top_k"]],
                ["Score threshold", config["score_threshold"]],
                ["Chunk window", config["chunk_window_seconds"]],
                ["Chunk overlap", config["chunk_overlap_seconds"]],
                ["Label source", config["label_source"]],
            ],
        ),
        "",
        "## Video Set",
        "",
        markdown_table(
            ["Video key", "Video id", "Duration seconds", "Segments", "Search architecture", "Chunks"],
            video_rows,
        ),
        "",
        "## How To Read A Result Row",
        "",
        f"Each row in `{display_path(artifact_paths['results'])}` is one labeled query against one processed video. "
        "`expected_start_seconds` and `expected_end_seconds` are the source-transcript timestamp window. "
        "`hit_at_1` is 1 only when the first returned segment is relevant. `hit_at_5` is 1 when any top-5 segment is relevant. "
        "`precision_at_5` is the number of relevant top-5 results divided by 5. `reciprocal_rank` is `1 / first_relevant_rank`, or 0 for a miss. "
        "`timestamp_error_seconds` is `abs(first_relevant_start - expected_start_seconds)` and is blank for misses.",
        "",
        "## Metrics",
        "",
        markdown_table(["Metric", "Value"], metric_rows),
        "",
        "Timestamp error is calculated only for queries with at least one relevant top-5 result.",
        "",
        "## Outcome Groups",
        "",
        markdown_table(["Category", "Count"], outcome_count_rows),
        "",
        "## Failure Groups",
        "",
        markdown_table(["Category", "Count"], failure_count_rows) if failure_count_rows else "No top-5 misses or low-rank failures.",
        "",
        "## Failure Samples",
        "",
        markdown_table(
            ["Label", "Video", "Query", "Expected", "Top result", "Score", "Category", "Top text"],
            failure_rows,
        ) if failure_rows else "No top-5 misses or low-rank failures.",
        "",
        "## Method Notes",
        "",
        *method_notes,
        "",
        "## Existing Processing Benchmark Context",
        "",
        "Keep this separate from query latency: the current processing benchmark shows "
        "`226.74s -> 40.73s`, a `5.6x` time-to-searchable improvement on the 20-minute video "
        "when moving from the old online baseline to chunked DAG processing with 4 search workers on an 8-vCPU EC2 instance.",
        "",
        "## Reproduce",
        "",
        "```powershell",
        config["command"],
        "```",
        "",
        "Artifacts:",
        "",
        f"- Labels: `{config['label_csv']}`",
        f"- Per-query CSV: `{display_path(artifact_paths['results'])}`",
        f"- Summary JSON: `{display_path(artifact_paths['summary'])}`",
        f"- Raw top-5 JSON: `{display_path(artifact_paths['raw'])}`",
        f"- Config JSON: `{display_path(artifact_paths['config'])}`",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Vindex semantic-search quality benchmark.")
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--token", default=None, help="Bearer token when auth is enabled.")
    parser.add_argument("--token-env", default=None, help="Environment variable containing the bearer token.")
    parser.add_argument("--token-file", type=Path, default=None, help="File containing the bearer token.")
    parser.add_argument("--algorithm", default="qdrant_embedding")
    parser.add_argument("--labels-csv", type=Path, default=LABELS_CSV)
    parser.add_argument("--video-ids-csv", type=Path, default=VIDEO_IDS_CSV)
    parser.add_argument("--results-output", type=Path, default=RESULTS_CSV)
    parser.add_argument("--raw-output", type=Path, default=RAW_RESULTS_JSON)
    parser.add_argument("--summary-output", type=Path, default=SUMMARY_JSON)
    parser.add_argument("--config-output", type=Path, default=CONFIG_JSON)
    parser.add_argument("--report-output", type=Path, default=REPORT_MD)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headers = build_headers(resolve_token(args))
    video_ids = latest_processed_video_ids(read_csv_rows(args.video_ids_csv))

    labels = [
        label for label in read_csv_rows(args.labels_csv)
        if label.get("query") and label.get("expected_start_seconds") and label.get("expected_end_seconds")
    ]

    rows = []
    raw_results = []
    for label in labels:
        video_id = video_ids.get(label["video_key"])
        if not video_id:
            raise RuntimeError(
                f"No processed video_id found for video_key={label['video_key']}. "
                "Run run_pipeline_benchmark.py first."
            )
        print(f"Searching {label['video_key']} label {label.get('label_id')}: {label['query']}")
        row, raw_entry = evaluate_label_with_raw(args=args, headers=headers, label=label, video_id=video_id)
        rows.append(row)
        raw_results.append(raw_entry)

    summary = summarize(rows)
    config = build_config(args=args, labels=labels, video_ids=video_ids, headers=headers)

    write_csv_rows(args.results_output, RESULT_FIELDNAMES, rows)
    write_json(args.summary_output, summary)
    write_json(args.raw_output, {"results": raw_results})
    write_json(args.config_output, config)
    write_report(
        args.report_output,
        summary,
        config,
        rows,
        {
            "results": args.results_output,
            "summary": args.summary_output,
            "raw": args.raw_output,
            "config": args.config_output,
        },
    )

    print(f"Wrote {display_path(args.results_output)}")
    print(f"Wrote {display_path(args.summary_output)}")
    print(f"Wrote {display_path(args.raw_output)}")
    print(f"Wrote {display_path(args.config_output)}")
    print(f"Wrote {display_path(args.report_output)}")


if __name__ == "__main__":
    main()
