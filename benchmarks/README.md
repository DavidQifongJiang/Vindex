# Vindex Benchmarks

This folder records Vindex pipeline and semantic-search benchmark data, including historical pipeline stages, the original semantic-search baseline, and the expanded 10-video search-quality benchmark.

The benchmark has two parts:

1. Pipeline performance: upload, processing, and time-to-searchable metrics.
2. Semantic search quality: Hit Rate@5, Precision@5, MRR, and timestamp error.

For the current pipeline redesign results, see [`pipeline_stats_summary.md`](pipeline_stats_summary.md). It summarizes the progression from the original worker, to the DAG split, to chunked search workers, to the 1/2/4 worker EC2 scaling experiment.

## Files

| File | Purpose |
| --- | --- |
| `videos.csv` | The benchmark videos to use. |
| `video_metadata.csv` | Generated metadata for the benchmark videos. |
| `semantic_search_labels.csv` | Manually curated semantic-search benchmark derived from source transcripts. |
| `current_video_ids.csv` | Generated map from benchmark video keys to uploaded Vindex video ids. |
| `baseline_current_pipeline.csv` | Generated pipeline performance results. |
| `baseline_search_quality.csv` | Generated per-query semantic search results. |
| `baseline_search_quality_raw.json` | Generated raw top-5 results and per-result relevance decisions. |
| `baseline_search_quality_summary.json` | Generated aggregate semantic search metrics. |
| `baseline_search_quality_config.json` | Generated frozen benchmark configuration. |
| `baseline_search_quality_report.md` | Generated human-readable semantic-search quality report. |
| `videos_expanded_15min.csv` | Expanded 10-video benchmark set using 15-minute lecture clips. |
| `video_metadata_expanded_15min.csv` | Generated metadata for the expanded benchmark videos. |
| `semantic_search_labels_expanded_15min.csv` | 100 manually curated labels for the expanded lecture benchmark. |
| `expanded_15min_search_quality.csv` | Generated per-query expanded semantic search results. |
| `expanded_15min_search_quality_raw.json` | Generated expanded raw top-5 results and relevance decisions. |
| `expanded_15min_search_quality_summary.json` | Generated expanded aggregate semantic search metrics. |
| `expanded_15min_search_quality_config.json` | Generated expanded frozen benchmark configuration. |
| `expanded_15min_search_quality_report.md` | Generated human-readable expanded semantic-search quality report. |

## 1. Inspect The Benchmark Videos

```powershell
python .\benchmarks\inspect_videos.py
```

This writes `video_metadata.csv` using the files listed in `videos.csv`.

## 2. Record Current Pipeline Performance

Start Vindex locally first, then run:

```powershell
python .\benchmarks\run_pipeline_benchmark.py --api-base-url http://localhost:8000
```

If auth is enabled, pass an id token:

```powershell
python .\benchmarks\run_pipeline_benchmark.py --api-base-url http://localhost:8000 --token YOUR_ID_TOKEN
```

The script uploads each benchmark video, waits until processing finishes or fails, then writes:

```text
baseline_current_pipeline.csv
current_video_ids.csv
```

## 3. Curate Search Labels

Edit `semantic_search_labels.csv`.

Each row should describe one realistic query and the expected timestamp range, based on the source transcript rather than on search output:

```text
label_id,video_key,query,expected_start_seconds,expected_end_seconds,relevance_window_seconds,query_type,expected_text,notes
```

For example:

```text
medium-001,medium,"where does the speaker explain memory benefits from deep sleep?",315,338,30,paraphrase,"file transfer mechanism; long-term storage","source transcript segments 1:4-1:8; manually curated from source transcript"
```

Describe the current benchmark label set as a manually curated benchmark derived from source transcripts. Reserve external-review wording only for a future benchmark that has actually gone through that process.

A search result counts as relevant when either:

- the returned segment overlaps the expected timestamp range, or
- the returned segment starts within `relevance_window_seconds` of `expected_start_seconds`.

Each generated result row in `baseline_search_quality.csv` is one query against one processed video. `hit_at_1` checks whether the first result is relevant, `hit_at_5` checks whether any top-5 result is relevant, `precision_at_5` divides the relevant top-5 count by 5, `reciprocal_rank` is `1 / first_relevant_rank`, and `timestamp_error_seconds` is the absolute distance between the first relevant result start and the expected start. Timestamp error is blank for misses.

## 4. Record Semantic Search Quality

After the benchmark videos are processed and `current_video_ids.csv` exists:

```powershell
python .\benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8000
```

If auth is enabled:

```powershell
python .\benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8000 --token YOUR_ID_TOKEN
```

This writes:

```text
baseline_search_quality.csv
baseline_search_quality_raw.json
baseline_search_quality_summary.json
baseline_search_quality_config.json
baseline_search_quality_report.md
```

For the expanded 10-video benchmark, process the expanded video set first:

```powershell
python .\benchmarks\run_pipeline_benchmark.py --api-base-url http://localhost:8000 --videos-csv .\benchmarks\videos_expanded_15min.csv --pipeline-output .\benchmarks\expanded_15min_pipeline_seed.csv --video-ids-output .\benchmarks\expanded_15min_video_ids.csv
```

Then pass the expanded label and video-id files:

```powershell
python .\benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8000 --labels-csv .\benchmarks\semantic_search_labels_expanded_15min.csv --video-ids-csv .\benchmarks\expanded_15min_video_ids.csv --results-output .\benchmarks\expanded_15min_search_quality.csv --raw-output .\benchmarks\expanded_15min_search_quality_raw.json --summary-output .\benchmarks\expanded_15min_search_quality_summary.json --config-output .\benchmarks\expanded_15min_search_quality_config.json --report-output .\benchmarks\expanded_15min_search_quality_report.md
```

Current expanded benchmark summary:

| Metric | Result |
| --- | ---: |
| Videos | 10 |
| Queries | 100 |
| Hit@1 | 65.0% |
| Hit@5 | 90.0% |
| Precision@5 | 43.4% |
| MRR | 0.746 |
| Median timestamp error | 16.1s |
| P95 timestamp error | 62.3s |
| P95 API search latency | 71.7ms |

## Metrics

Hit Rate@5:

```text
queries with at least one relevant top-5 result / total labeled queries
```

Hit@1:

```text
queries where the first result is relevant / total labeled queries
```

Precision@5:

```text
relevant top-5 results / 5
```

MRR:

```text
average of 1 / rank_of_first_relevant_result
```

Timestamp Error:

```text
abs(first_relevant_result_start - expected_start_seconds)
```

For missed queries, timestamp error is blank and excluded from median/p95 timestamp-error summaries.
