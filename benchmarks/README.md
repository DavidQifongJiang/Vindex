# Vindex Benchmarks

This folder records the current Vindex baseline before the pipeline DAG redesign.

The benchmark has two parts:

1. Pipeline performance: upload, processing, and time-to-searchable metrics.
2. Semantic search quality: Hit Rate@5, Precision@5, MRR, and timestamp error.

For the current pipeline redesign results, see [`pipeline_stats_summary.md`](pipeline_stats_summary.md). It summarizes the progression from the original worker, to the DAG split, to chunked search workers, to the 1/2/4 worker EC2 scaling experiment.

## Files

| File | Purpose |
| --- | --- |
| `videos.csv` | The benchmark videos to use. |
| `video_metadata.csv` | Generated metadata for the benchmark videos. |
| `semantic_search_labels.csv` | Human-labeled query set for semantic search quality. |
| `current_video_ids.csv` | Generated map from benchmark video keys to uploaded Vindex video ids. |
| `baseline_current_pipeline.csv` | Generated pipeline performance results. |
| `baseline_search_quality.csv` | Generated per-query semantic search results. |
| `baseline_search_quality_summary.json` | Generated aggregate semantic search metrics. |

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

## 3. Add Human Search Labels

Edit `semantic_search_labels.csv`.

Each row should describe one realistic query and the expected timestamp range:

```text
label_id,video_key,query,expected_start_seconds,expected_end_seconds,relevance_window_seconds,notes
```

For example:

```text
medium-001,medium,"where does the speaker explain vector search?",430,500,30,"Expected answer range approved by human review."
```

A search result counts as relevant when either:

- the returned segment overlaps the expected timestamp range, or
- the returned segment starts within `relevance_window_seconds` of `expected_start_seconds`.

## 4. Record Semantic Search Quality

After the benchmark videos are processed and `current_video_ids.csv` exists:

```powershell
python .\benchmarks\run_search_quality_benchmark.py --api-base-url http://localhost:8000
```

This writes:

```text
baseline_search_quality.csv
baseline_search_quality_summary.json
```

## Metrics

Hit Rate@5:

```text
queries with at least one relevant top-5 result / total labeled queries
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
