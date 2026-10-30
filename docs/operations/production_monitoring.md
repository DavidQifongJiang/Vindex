# Vindex Production Monitoring Plan

Vindex should alert on user-facing outcomes first, then use worker and dependency metrics to explain the cause. The current product already records stage-level metrics for upload, processing, search indexing, and query latency. Production monitoring should turn those metrics into SLOs and alerts.

## Alerting Principles

- Alert on symptoms users feel: upload failures, videos not becoming searchable, and slow or failing search.
- Use dynamic processing thresholds because a 2-minute clip, 20-minute upload, and 2-hour lecture should not share one fixed timeout.
- Keep alerts actionable. Every alert should point to an owner and a likely first debugging step.
- Page only on user-impacting or data-loss risks. Send lower-priority notifications for early warning signals.

## Dynamic Time-To-Searchable Alert

The most important Vindex alert is time-to-searchable because it measures when an uploaded video becomes useful.

Use an expected processing-time model instead of a single fixed threshold:

```text
expected_time_to_searchable =
fixed_overhead_seconds
+ queue_wait_seconds
+ expected_processing_waves * p95_chunk_processing_seconds
+ upload_size_factor_seconds
```

For the current chunked DAG:

```text
chunk_count = ceil(video_duration_seconds / SEARCH_CHUNK_SECONDS)
expected_processing_waves = ceil(chunk_count / active_search_workers)
```

MVP thresholds:

| Severity | Rule |
| --- | --- |
| Warning | `actual_time_to_searchable > 1.5x expected_time_to_searchable` |
| Critical | `actual_time_to_searchable > 2.5x expected_time_to_searchable` |
| Stuck video | no chunk or status progress for 10-15 minutes |
| Failed video | any video enters `failed` after retries are exhausted |

This scales naturally with video length. A longer video gets more chunks and more expected processing waves, while a short clip is still expected to become searchable quickly.

## User-Facing Alerts

| Alert | MVP Threshold | Why It Matters |
| --- | --- | --- |
| Upload failure rate | `> 5%` over 10 minutes | Users cannot enter the pipeline. |
| Upload p95 latency | `> 2x baseline for comparable file size` | Upload path or S3 may be degraded. |
| Time-to-searchable p95 | `> 1.5x expected` warning, `> 2.5x expected` critical | Core product promise is degrading. |
| Search API 5xx rate | `> 2%` over 5 minutes | Search is failing after indexing. |
| Search API p95 latency | `> 500ms` over 10 minutes | Search feels slow to users. |
| Empty result rate | sharp increase from baseline | Retrieval or indexing quality may have regressed. |

## Worker And Pipeline Alerts

| Alert | MVP Threshold | First Debugging Direction |
| --- | --- | --- |
| Celery queue age | oldest search chunk waits `> 5 minutes` | Not enough workers, stalled worker, or CPU saturation. |
| Queue depth | pending chunks keep increasing for 10 minutes | Workers cannot drain incoming work. |
| Chunk failure rate | `> 5%` over 30 minutes | Check FFmpeg, Whisper, embedding, S3, and Qdrant errors. |
| Retry exhaustion | any chunk exhausts retries | One video may never become searchable. |
| Stuck chunks | chunk in `processing` with no progress for 10-15 minutes | Worker crash or lost task. |
| Worker heartbeat | no active worker heartbeat for 2-3 minutes | Background processing is down. |
| CPU saturation | `> 85%` for 10 minutes | Add CPU, lower concurrency, or tune chunk size. |
| Memory saturation | `> 85%` for 10 minutes | Whisper or FFmpeg may crash or swap. |

## Dependency Alerts

| Dependency | Alert |
| --- | --- |
| S3 | elevated upload/download/write failures or p95 latency above baseline |
| Redis | broker unavailable, queue depth not changing, or connection failures |
| Postgres | connection saturation, failed queries, or slow status updates |
| Qdrant | search/upsert errors, high p95 query latency, or failed vector writes |
| FFmpeg | transcode or audio extraction failures |
| Whisper | model load failures or transcription time per audio minute above baseline |
| Embedding model | model load failures or embedding generation time above baseline |

## Stage-Level Ratios

Raw seconds are useful, but ratios are better for alerting across different file sizes and durations.

| Stage | Elastic Metric |
| --- | --- |
| Upload | `upload_seconds_per_gb` |
| Queue | `oldest_queued_chunk_age_seconds` |
| Transcode | `video_transcode_seconds / video_duration_minutes` |
| Audio extraction | `audio_extraction_seconds / video_duration_minutes` |
| Transcription | `whisper_transcription_seconds / audio_duration_minutes` |
| Embedding | `embedding_generation_seconds / segment_count` |
| Qdrant upsert | `qdrant_upsert_seconds / vector_count` |
| Full pipeline | `actual_time_to_searchable / expected_time_to_searchable` |

## Interview Summary

I instrumented Vindex with stage-level metrics across upload, processing, transcription, embedding, Qdrant, and search. For production alerting, I would start with user-facing SLOs: upload success, search latency, and especially time-to-searchable. Because videos vary in length and size, I would not use a fixed processing timeout. I would calculate an expected time-to-searchable from duration, chunk count, active workers, queue wait, and historical p95 chunk time, then alert when actual processing exceeds that expectation by a meaningful ratio. Worker health, queue age, chunk failure rate, retry exhaustion, and dependency errors would explain the cause when the user-facing SLO is violated.
