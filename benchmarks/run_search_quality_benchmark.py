from __future__ import annotations

import argparse
import json
import time
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

RESULT_FIELDNAMES = [
    "label_id",
    "video_key",
    "video_id",
    "query",
    "expected_start_seconds",
    "expected_end_seconds",
    "relevance_window_seconds",
    "result_count",
    "hit_at_5",
    "precision_at_5",
    "reciprocal_rank",
    "first_relevant_rank",
    "timestamp_error_seconds",
    "top_result_start",
    "top_result_end",
    "top_result_score",
    "request_latency_seconds",
    "api_search_latency_seconds",
]


def build_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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


def evaluate_label(
    *,
    args: argparse.Namespace,
    headers: dict[str, str],
    label: dict[str, str],
    video_id: str,
) -> dict[str, Any]:
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

    results = payload.get("results", [])[:5]
    relevant_flags = [
        is_relevant(result, expected_start, expected_end, relevance_window)
        for result in results
    ]

    relevant_count = sum(1 for value in relevant_flags if value)
    hit_at_5 = 1 if relevant_count > 0 else 0
    precision_at_5 = relevant_count / 5

    first_relevant_rank = ""
    timestamp_error = ""
    reciprocal_rank = 0.0
    for index, is_match in enumerate(relevant_flags, start=1):
        if is_match:
            first_relevant_rank = index
            reciprocal_rank = 1 / index
            result_start = safe_float(results[index - 1].get("start"))
            if result_start is not None:
                timestamp_error = round(abs(result_start - expected_start), 4)
            break

    top_result = results[0] if results else {}
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}

    return {
        "label_id": label.get("label_id", ""),
        "video_key": label["video_key"],
        "video_id": video_id,
        "query": label["query"],
        "expected_start_seconds": expected_start,
        "expected_end_seconds": expected_end,
        "relevance_window_seconds": relevance_window,
        "result_count": len(results),
        "hit_at_5": hit_at_5,
        "precision_at_5": round(precision_at_5, 4),
        "reciprocal_rank": round(reciprocal_rank, 4),
        "first_relevant_rank": first_relevant_rank,
        "timestamp_error_seconds": timestamp_error,
        "top_result_start": top_result.get("start", ""),
        "top_result_end": top_result.get("end", ""),
        "top_result_score": top_result.get("score", ""),
        "request_latency_seconds": request_latency,
        "api_search_latency_seconds": metrics.get("search_latency_seconds", ""),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {
            "total_labeled_queries": 0,
            "message": "No filled labels were found.",
        }

    hit_values = [safe_float(row["hit_at_5"]) or 0.0 for row in rows]
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

    return {
        "total_labeled_queries": total,
        "hit_rate_at_5": round(sum(hit_values) / total, 4),
        "mean_precision_at_5": round(sum(precision_values) / total, 4),
        "mrr": round(sum(rr_values) / total, 4),
        "median_timestamp_error_seconds": percentile(timestamp_errors, 0.5),
        "p95_timestamp_error_seconds": percentile(timestamp_errors, 0.95),
        "median_request_latency_seconds": percentile(request_latencies, 0.5),
        "p95_request_latency_seconds": percentile(request_latencies, 0.95),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Vindex semantic-search quality benchmark.")
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--token", default=None, help="Bearer token when auth is enabled.")
    parser.add_argument("--algorithm", default="qdrant_embedding")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headers = build_headers(args.token)
    video_ids = latest_processed_video_ids(read_csv_rows(VIDEO_IDS_CSV))

    labels = [
        label for label in read_csv_rows(LABELS_CSV)
        if label.get("query") and label.get("expected_start_seconds") and label.get("expected_end_seconds")
    ]

    rows = []
    for label in labels:
        video_id = video_ids.get(label["video_key"])
        if not video_id:
            raise RuntimeError(
                f"No processed video_id found for video_key={label['video_key']}. "
                "Run run_pipeline_benchmark.py first."
            )
        print(f"Searching {label['video_key']} label {label.get('label_id')}: {label['query']}")
        rows.append(evaluate_label(args=args, headers=headers, label=label, video_id=video_id))

    write_csv_rows(RESULTS_CSV, RESULT_FIELDNAMES, rows)
    write_json(SUMMARY_JSON, summarize(rows))

    print(f"Wrote {RESULTS_CSV.relative_to(Path.cwd())}")
    print(f"Wrote {SUMMARY_JSON.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
