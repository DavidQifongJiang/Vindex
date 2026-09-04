# Vindex Semantic Search Quality Benchmark

Generated at: `2026-09-04T00:15:31.051529+00:00`

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
| lecture01_peak_finding | cab675d5-8d03-4c20-9223-f08d149c2338 | 900.0107 | 249 | chunked | 4 |
| lecture02_models_distance | d142ffc4-f27c-4676-a73c-5220996cad70 | 900.0107 | 290 | chunked | 4 |
| lecture03_sorting | a7e7823b-9b71-4b7a-9db9-49729b7fae94 | 900.0107 | 215 | chunked | 4 |
| lecture04_heaps | 118ac200-0487-4e01-8c40-84999da3c9a6 | 900.0107 | 184 | chunked | 4 |
| lecture05_bst | 60ff55a2-7f9b-4ea3-9942-5d8d15f6eaef | 900.0107 | 231 | chunked | 4 |
| lecture06_avl | 1bb04e42-835e-40ea-b985-06bd83e81462 | 900.0107 | 282 | chunked | 4 |
| lecture07_counting_radix | 06a50f4f-952c-4f2b-b0dc-e38bfdc38012 | 900.0107 | 240 | chunked | 4 |
| lecture08_hashing_chaining | ba204e1c-d834-47cc-8a2c-4eff561ea2f0 | 900.0107 | 270 | chunked | 4 |
| lecture09_table_karp | 1f663c0b-710d-4c7d-8f0d-7b0f696af6a8 | 900.0107 | 271 | chunked | 4 |
| lecture10_open_crypto | 22f57e66-2022-48f8-b34a-fef6855712a8 | 900.0107 | 218 | chunked | 4 |

## How To Read A Result Row

Each row in `benchmarks\expanded_15min_search_quality.csv` is one labeled query against one processed video. `expected_start_seconds` and `expected_end_seconds` are the source-transcript timestamp window. `hit_at_1` is 1 only when the first returned segment is relevant. `hit_at_5` is 1 when any top-5 segment is relevant. `precision_at_5` is the number of relevant top-5 results divided by 5. `reciprocal_rank` is `1 / first_relevant_rank`, or 0 for a miss. `timestamp_error_seconds` is `abs(first_relevant_start - expected_start_seconds)` and is blank for misses.

## Metrics

| Metric | Value |
| --- | --- |
| Queries | 100 |
| Hit@1 | 0.6500 |
| Hit@5 | 0.9000 |
| Precision@5 | 0.4340 |
| MRR | 0.7457 |
| Mean timestamp error, hits only | 23.0409 |
| Median timestamp error, hits only | 16.1200 |
| P90 timestamp error, hits only | 52.8320 |
| P95 timestamp error, hits only | 62.3320 |
| P50 request latency | 0.2373 |
| P95 request latency | 0.3562 |
| P50 API search latency | 0.0629 |
| P95 API search latency | 0.0717 |

Timestamp error is calculated only for queries with at least one relevant top-5 result.

## Outcome Groups

| Category | Count |
| --- | --- |
| hit_at_1 | 65 |
| low_rank | 25 |
| topic_confusion | 10 |

## Failure Groups

| Category | Count |
| --- | --- |
| low_rank | 25 |
| topic_confusion | 10 |

## Failure Samples

| Label | Video | Query | Expected | Top result | Score | Category | Top text |
| --- | --- | --- | --- | --- | --- | --- | --- |
| lecture10-001 | lecture10_open_crypto | open addressing is the simplest way to implement a hash table | 60.0-120.0 | 314.92-318.16 | 0.9192 | topic_confusion | to work in open-addressing hash table, |
| lecture04-003 | lecture04_heaps | a heap is the ADT implementation for priority queues | 180.0-240.0 | 373.16-380.6 | 0.8242 | topic_confusion | A heap is an implementation of a priority queue. It is amazingly an arrays structure |
| lecture03-003 | lecture03_sorting | sorting makes finding the median easy | 120.0-180.0 | 305.0-311.0 | 0.7814 | topic_confusion | of finding the median, but it's constant time if you have a sorted list. |
| lecture06-005 | lecture06_avl | worst case binary search trees are not balanced | 300.0-360.0 | 212.24-214.44 | 0.7018 | topic_confusion | This is a nice perfectly balanced binary search |
| lecture08-009 | lecture08_hashing_chaining | programming languages need dictionaries for variable names | 540.0-660.0 | 420.44-422.48 | 0.6627 | topic_confusion | As a result, dictionaries are built into basically |
| lecture05-006 | lecture05_bst | at time thirty seven the reservation set has landing times | 360.0-420.0 | 315.68-320.2 | 0.6491 | topic_confusion | which is the set of landing times after the plane lands. |
| lecture03-004 | lecture03_sorting | sorting needs a comparison function for records | 180.0-240.0 | 51.96-54.48 | 0.6327 | topic_confusion | It's not the best sorting algorithm that's out there. |
| lecture05-007 | lecture05_bst | landing time fifty three is allowed but forty four is too close | 420.0-480.0 | 200.32-202.6 | 0.6086 | topic_confusion | Each of them is going to specify a landing time. |
| lecture06-009 | lecture06_avl | local formulas let augmented search trees maintain extra information | 600.0-660.0 | 25.6-27.52 | 0.6074 | topic_confusion | but in particular binary search trees, |
| lecture10-008 | lecture10_open_crypto | the probing sequence should be a permutation of all table slots | 540.0-600.0 | 655.56-660.56 | 0.5523 | topic_confusion | load balancing the table and ensuring that all slots in the table |

## Method Notes

- Labels are manually curated from the source transcripts and have not gone through external reviewer adjudication.
- The benchmark evaluates the current semantic-search implementation only; it does not redesign retrieval, chunk scoring, ranking, or thresholds.
- Precision@5 uses a fixed denominator of 5, even when multiple nearby segments are arguably acceptable to a user.
- Timestamp error excludes top-5 misses, so pair it with Hit@5 instead of reading it alone.
- The local benchmark was run through a temporary dev-user API on port 8001 because the normal local API on port 8000 required Cognito auth.
- This expanded run uses the first 15 minutes of each long lecture, so it validates lecture-search behavior but not full-video coverage.

## Existing Processing Benchmark Context

Keep this separate from query latency: the current processing benchmark shows `226.74s -> 40.73s`, a `5.6x` time-to-searchable improvement on the 20-minute video when moving from the old online baseline to chunked DAG processing with 4 search workers on an 8-vCPU EC2 instance.

## Reproduce

```powershell
python benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8001 --labels-csv benchmarks\semantic_search_labels_expanded_15min.csv --video-ids-csv benchmarks\expanded_15min_video_ids.csv --results-output benchmarks\expanded_15min_search_quality.csv --raw-output benchmarks\expanded_15min_search_quality_raw.json --summary-output benchmarks\expanded_15min_search_quality_summary.json --config-output benchmarks\expanded_15min_search_quality_config.json --report-output benchmarks\expanded_15min_search_quality_report.md
```

Artifacts:

- Labels: `benchmarks\semantic_search_labels_expanded_15min.csv`
- Per-query CSV: `benchmarks\expanded_15min_search_quality.csv`
- Summary JSON: `benchmarks\expanded_15min_search_quality_summary.json`
- Raw top-5 JSON: `benchmarks\expanded_15min_search_quality_raw.json`
- Config JSON: `benchmarks\expanded_15min_search_quality_config.json`
