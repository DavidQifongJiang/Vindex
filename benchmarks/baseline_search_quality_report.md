# Vindex Semantic Search Quality Baseline

Generated at: `2026-09-03T23:05:06.507965+00:00`

## Frozen Config

| Setting | Value |
| --- | --- |
| API base URL | http://localhost:8001 |
| Auth mode | dev_user_or_public_api |
| Algorithm | qdrant_embedding |
| Embedding model | all-MiniLM-L6-v2 |
| Vector DB / collection | Qdrant / vindex_segments |
| Vector size | 384 |
| Similarity metric | cosine |
| Top-k | 5 |
| Score threshold | none |
| Chunk window | 300 |
| Chunk overlap | 5 |
| Label source | manually curated benchmark derived from source transcripts |

## Video Set

| Video key | Video id | Duration seconds | Segments | Search architecture | Chunks |
| --- | --- | --- | --- | --- | --- |
| small | ac04a8ca-db5e-46ac-b9c0-b72bf601fb9f | 120.6000 | 14 | chunked | 1 |
| medium | d0005a3b-478d-4737-b434-050c177c1e00 | 1158.1881 | 192 | chunked | 4 |

## How To Read A Result Row

Each row in `baseline_search_quality.csv` is one labeled query against one processed video. `expected_start_seconds` and `expected_end_seconds` are the source-transcript timestamp window. `hit_at_1` is 1 only when the first returned segment is relevant. `hit_at_5` is 1 when any top-5 segment is relevant. `precision_at_5` is the number of relevant top-5 results divided by 5. `reciprocal_rank` is `1 / first_relevant_rank`, or 0 for a miss. `timestamp_error_seconds` is `abs(first_relevant_start - expected_start_seconds)` and is blank for misses.

## Metrics

| Metric | Value |
| --- | --- |
| Queries | 50 |
| Hit@1 | 0.8600 |
| Hit@5 | 0.9600 |
| Precision@5 | 0.4840 |
| MRR | 0.9050 |
| Mean timestamp error, hits only | 7.8708 |
| Median timestamp error, hits only | 6.7200 |
| P90 timestamp error, hits only | 16.0240 |
| P95 timestamp error, hits only | 20.8460 |
| P50 request latency | 0.2174 |
| P95 request latency | 0.3344 |
| P50 API search latency | 0.0649 |
| P95 API search latency | 0.0791 |

Timestamp error is calculated only for queries with at least one relevant top-5 result.

## Outcome Groups

| Category | Count |
| --- | --- |
| hit_at_1 | 43 |
| low_rank | 5 |
| topic_confusion | 2 |

## Failure Groups

| Category | Count |
| --- | --- |
| low_rank | 5 |
| topic_confusion | 2 |

## Failure Samples

| Label | Video | Query | Expected | Top result | Score | Category | Top text |
| --- | --- | --- | --- | --- | --- | --- | --- |
| medium-034 | medium | lack of sleep erodes DNA and the genetic code | 743.4-758.44 | 811.32-816.76 | 0.7937 | topic_confusion | In contrast, those genes that were actually upregulated or increased by way of a lack o... |
| medium-029 | medium | one night of four hours sleep causes a seventy percent drop in natural killer activity | 635.8-662.12 | 555.12-562.4 | 0.6044 | topic_confusion | Now in the spring when we lose one hour of sleep we see a subsequent 24% increase in |
| medium-044 | medium | reclaim a full night of sleep as the Swiss Army knife of health | 992.0-1018.0 | 945.0-951.0 | 0.6604 | low_rank | Sleep unfortunately is not an optional lifestyle luxury. |
| medium-016 | medium | aging dementia and poor deep sleep are significantly interrelated | 348.88-408.44 | 429.6-435.76 | 0.6547 | low_rank | But that sleep is a missing piece in the explanatory puzzle of aging and Alzheimer's. |
| medium-015 | medium | deep sleep transfers memories from short term to long term storage | 315.2-337.6 | 93.04-99.52 | 0.6460 | low_rank | sleep after learning to essentially hit the save button on those new memories so that y... |
| medium-030 | medium | short sleep duration is linked to bowel prostate and breast cancer | 669.24-687.72 | 730.92-734.36 | 0.6015 | low_rank | Short sleep predicts all cause mortality. |
| small-004 | small | the talk turns to bad effects on brain and body | 68.4-86.96 | 116.32-120.6 | 0.3299 | low_rank | the memory circuits of the brain essentially become waterlogged as it. |

## Method Notes

- Labels are manually curated from the source transcripts and have not gone through external reviewer adjudication.
- The benchmark evaluates the current semantic-search implementation only; it does not redesign retrieval, chunk scoring, ranking, or thresholds.
- Precision@5 uses a fixed denominator of 5, even when multiple nearby segments are arguably acceptable to a user.
- Timestamp error excludes top-5 misses, so pair it with Hit@5 instead of reading it alone.
- The local benchmark was run through a temporary dev-user API on port 8001 because the normal local API on port 8000 required Cognito auth.
- The short video is a 2-minute subset of the same talk, so it validates short-video behavior but is not independent topical coverage.

## Existing Processing Benchmark Context

Keep this separate from query latency: the current processing benchmark shows `226.74s -> 40.73s`, a `5.6x` time-to-searchable improvement on the 20-minute video when moving from the old online baseline to chunked DAG processing with 4 search workers on an 8-vCPU EC2 instance.

## Reproduce

```powershell
python benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8001 --video-ids-csv benchmarks\semantic_search_video_ids.csv
```

Artifacts:

- Labels: `benchmarks\semantic_search_labels.csv`
- Per-query CSV: `benchmarks\baseline_search_quality.csv`
- Summary JSON: `benchmarks\baseline_search_quality_summary.json`
- Raw top-5 JSON: `benchmarks\baseline_search_quality_raw.json`
- Config JSON: `benchmarks\baseline_search_quality_config.json`
