# Vindex reliability map

Working-tree audit dated October 26, 2026. This extends the compact upload diagram with the implemented retry, idempotency, failure, cleanup, optional indexing, analytics, and operational recovery mechanisms.

Open [the visual guide](index.html). Each numbered panel has an editable Mermaid source and a copy button. Paste the source onto an empty area of the Whimsical board. The combined source imports all sections, but Whimsical chooses their placement; separate panel pastes give the most readable board layout. Suggested order: core panels 01–08, optional search 09–10, optional analytics 11–13, operations and limits 14–16.

The diagrams are an implementation map, not a record of newly run integration tests or benchmark acceptance. Optional paths require their feature configuration. All timing/budget values shown are code defaults unless explicitly described as constants.

## Source and test traceability

### 00 · Reliability overview

**Scope:** Core and optional paths.

[Diagram source](00-overview.mmd) · [Vector preview](00-overview.svg)

Solid arrows describe the main lifecycle. Numbered references lead to detailed panels. Postgres is the authority; Redis and Kafka carry messages. No exactly-once execution or cross-system rollback is claimed.

Implementation:

- [app/db/upload_repository.py](../../../app/db/upload_repository.py)
- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/db/search_repository.py](../../../app/db/search_repository.py)
- [app/db/search_event_repository.py](../../../app/db/search_event_repository.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)
- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)
- [tests/test_search_indexing.py](../../../tests/test_search_indexing.py)
- [tests/test_search_events.py](../../../tests/test_search_events.py)

### 01 · Upload idempotency

**Scope:** Core.

[Diagram source](01-upload-idempotency.mmd) · [Vector preview](01-upload-idempotency.svg)

The frontend retains a UUID for the same File object, title and visibility; a reload or new file selection is a new intent. No automatic upload retry loop is implemented. Without a supplied key the server generates a new key for that request. This is request deduplication, not global content deduplication.

Implementation:

- [frontend/src/api.ts](../../../frontend/src/api.ts)
- [app/api/videos.py](../../../app/api/videos.py)
- [app/db/upload_repository.py](../../../app/db/upload_repository.py)
- [app/db/video_repository.py](../../../app/db/video_repository.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)

### 02 · Durable dependencies and dispatch

**Scope:** Core.

[Diagram source](02-durable-dag-dispatch.mmd) · [Vector preview](02-durable-dag-dispatch.svg)

Redis delivery is at least once. Dispatch reservation does not increment the processing attempt count; a successful database claim does. The dispatcher is required for new uploads. Search, chunk and finalizer tasks use the search queue; playback uses playback; optional projections use indexing.

Implementation:

- [app/db/video_repository.py](../../../app/db/video_repository.py)
- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/workers/pipeline_dispatcher.py](../../../app/workers/pipeline_dispatcher.py)
- [app/workers/celery_app.py](../../../app/workers/celery_app.py)
- [app/db/models.py](../../../app/db/models.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)

### 03 · Claims, leases and deadlines

**Scope:** Core.

[Diagram source](03-claim-lease-deadline.mmd) · [Vector preview](03-claim-lease-deadline.svg)

No database transaction stays open across media/model work. Lease renewal and publication require both a live lease and a live absolute deadline. Ordinary failure recording checks the matching token and a live lease; expired-work recovery has a separate guarded path. Terminal video status alone does not cancel already pending sibling work.

Implementation:

- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/workers/durable_task.py](../../../app/workers/durable_task.py)
- [app/services/processing_lease.py](../../../app/services/processing_lease.py)
- [app/services/media_service.py](../../../app/services/media_service.py)
- [app/workers/celery_app.py](../../../app/workers/celery_app.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)
- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)

### 04 · Error classification

**Scope:** Core and reused by projection workers.

[Diagram source](04-error-classification.mmd) · [Vector preview](04-error-classification.svg)

Classification determines whether another attempt is allowed; panel 05 also checks the persisted budget. Dotted links between error boxes group alternative cases rather than showing an execution sequence. Existing ProcessingError subclasses retain their classification. Wrapped S3 transfers are classified using their last exception/cause/context when available. Unknown exceptions are terminal rather than retried indefinitely.

Implementation:

- [app/services/processing_errors.py](../../../app/services/processing_errors.py)
- [app/services/video_service.py](../../../app/services/video_service.py)
- [app/services/storage_service.py](../../../app/services/storage_service.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)
- [tests/test_video_repository_retry_state.py](../../../tests/test_video_repository_retry_state.py)

### 05 · Retry state and expired-work recovery

**Scope:** Core.

[Diagram source](05-retry-and-recovery.mmd) · [Vector preview](05-retry-and-recovery.svg)

Three attempts means three total successful claims, not three extra retries. A fresh Celery message with request.retries == 0 does not reset this budget. Retry is a Postgres state transition; durable tasks do not call Celery task.retry. Recovery latency includes detection, polling, backoff, and queue delay.

Implementation:

- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/services/retry_policy.py](../../../app/services/retry_policy.py)
- [app/workers/pipeline_dispatcher.py](../../../app/workers/pipeline_dispatcher.py)
- [app/workers/durable_task.py](../../../app/workers/durable_task.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)
- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)

### 06 · Stage idempotency and guarded publication

**Scope:** Core.

[Diagram source](06-publication-and-stages.mmd) · [Vector preview](06-publication-and-stages.svg)

Fencing protects accepted metadata and read visibility, not every external write. S3, Qdrant and Postgres do not share a transaction. Each new attempt has isolated outputs; repeating a write within one attempt uses deterministic keys/IDs. A stale attempt can leave garbage for panel 08.

Implementation:

- [app/services/video_service.py](../../../app/services/video_service.py)
- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/services/qdrant_service.py](../../../app/services/qdrant_service.py)
- [app/db/video_repository.py](../../../app/db/video_repository.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)
- [tests/test_fenced_vectors.py](../../../tests/test_fenced_vectors.py)

### 07 · Failure windows and recovery paths

**Scope:** Core; scenarios are independent.

[Diagram source](07-crash-windows.mmd) · [Vector preview](07-crash-windows.svg)

These are independent recovery scenarios, not an execution chain or claims of production validation. Dotted connectors organize related cases vertically. No live service or benchmark is started to generate this board.

Implementation:

- [app/db/upload_repository.py](../../../app/db/upload_repository.py)
- [app/workers/pipeline_dispatcher.py](../../../app/workers/pipeline_dispatcher.py)
- [app/workers/durable_task.py](../../../app/workers/durable_task.py)
- [app/db/pipeline_repository.py](../../../app/db/pipeline_repository.py)
- [app/services/processing_lease.py](../../../app/services/processing_lease.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)
- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)
- [tests/lease_crash_worker.py](../../../tests/lease_crash_worker.py)

### 08 · Deletion, orphan cleanup and vector repair

**Scope:** Core.

[Diagram source](08-delete-gc-repair.mmd) · [Vector preview](08-delete-gc-repair.svg)

Deletion is not a distributed transaction. Some raw orphan paths are collected by maintenance rather than the endpoint. Unknown path formats are intentionally retained. Core Qdrant repair detects missing counts; it does not verify every vector value, restore missing S3 objects, or restart terminally failed processing.

Implementation:

- [app/api/videos.py](../../../app/api/videos.py)
- [app/services/maintenance_service.py](../../../app/services/maintenance_service.py)
- [app/services/storage_service.py](../../../app/services/storage_service.py)
- [app/workers/maintenance.py](../../../app/workers/maintenance.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)
- [tests/test_durable_pipeline.py](../../../tests/test_durable_pipeline.py)

### 09 · Optional library projection jobs

**Scope:** OPTIONAL · SEARCH_PROJECTIONS_ENABLED.

[Diagram source](09-search-projection-recovery.mmd) · [Vector preview](09-search-projection-recovery.svg)

Projection failure does not invalidate the uploaded video. These jobs have their own state machine and accepted attempt per backend. Bad source/schema/model data needs explicit repair. This system is separate from core vector-count repair and from legacy lexical reconciliation.

Implementation:

- [app/db/search_repository.py](../../../app/db/search_repository.py)
- [app/services/search_indexing.py](../../../app/services/search_indexing.py)
- [app/services/projection_indexes.py](../../../app/services/projection_indexes.py)
- [app/workers/search_indexing.py](../../../app/workers/search_indexing.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_search_indexing.py](../../../tests/test_search_indexing.py)
- [tests/search_projection_crash.py](../../../tests/search_projection_crash.py)

### 10 · Optional search failure policy and legacy lexical repair

**Scope:** OPTIONAL · separate feature flags.

[Diagram source](10-search-serving-and-lexical.mmd) · [Vector preview](10-search-serving-and-lexical.svg)

Search results are revalidated against fresh Postgres state before return. A successful empty result differs from a failed backend. The legacy lexical reconciler runs only when Elasticsearch is configured and durable projections are disabled; it has different repair behavior.

Implementation:

- [app/services/library_search.py](../../../app/services/library_search.py)
- [app/services/search_policy.py](../../../app/services/search_policy.py)
- [app/api/search.py](../../../app/api/search.py)
- [app/services/lexical_index.py](../../../app/services/lexical_index.py)
- [app/services/lexical_reconciliation.py](../../../app/services/lexical_reconciliation.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_library_search.py](../../../tests/test_library_search.py)
- [tests/test_lexical_index.py](../../../tests/test_lexical_index.py)

### 11 · Optional Kafka capture and interaction idempotency

**Scope:** OPTIONAL · opt-in analytics prototype.

[Diagram source](11-event-capture-and-feedback.mmd) · [Vector preview](11-event-capture-and-feedback.svg)

No broker call occurs on the search request path. Optional capture may be skipped or fail before durable acceptance; a 25ms timeout is an unknown outcome because the bounded worker may commit later. Late capture does not enable feedback on an already returned response. Kafka contains content-free events, not raw queries.

Implementation:

- [app/services/search_events.py](../../../app/services/search_events.py)
- [app/db/search_event_repository.py](../../../app/db/search_event_repository.py)
- [app/api/search.py](../../../app/api/search.py)
- [app/api/search_analytics.py](../../../app/api/search_analytics.py)
- [frontend/src/LibrarySearch.tsx](../../../frontend/src/LibrarySearch.tsx)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_search_events.py](../../../tests/test_search_events.py)
- [tests/test_search_events_integration.py](../../../tests/test_search_events_integration.py)
- [tests/search_analytics_browser.cjs](../../../tests/search_analytics_browser.cjs)

### 12 · Optional Kafka publisher and outbox expiry

**Scope:** OPTIONAL · analytics prototype.

[Diagram source](12-event-publisher.mmd) · [Vector preview](12-event-publisher.svg)

Producer idempotence does not make the Postgres-to-Kafka bridge exactly once. A crash after broker acceptance but before marking the outbox can republish the same event. Consumer receipts handle those duplicates. Seven-day expiry bounds retention; accepted events can expire during a sufficiently long outage.

Implementation:

- [app/workers/search_events.py](../../../app/workers/search_events.py)
- [app/db/search_event_repository.py](../../../app/db/search_event_repository.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_search_events.py](../../../tests/test_search_events.py)
- [tests/test_search_events_integration.py](../../../tests/test_search_events_integration.py)

### 13 · Optional Kafka consumers, quarantine and replay

**Scope:** OPTIONAL · analytics prototype.

[Diagram source](13-consumer-replay.mmd) · [Vector preview](13-consumer-replay.svg)

Analytics and review use independent consumer groups and receipt generations. Receipt identity is generation + event_id. Deduplication applies to database effects within a generation, not producer capture. An absent broker offset is allowed only when the DB checkpoint equals the retained low watermark; a broker offset without a DB checkpoint fails closed. Replay creates a separate shadow and never replaces the live dashboard.

Implementation:

- [app/workers/search_events.py](../../../app/workers/search_events.py)
- [app/db/search_event_repository.py](../../../app/db/search_event_repository.py)
- [app/db/search_event_models.py](../../../app/db/search_event_models.py)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_search_event_worker.py](../../../tests/test_search_event_worker.py)
- [tests/test_search_events_rebalance.py](../../../tests/test_search_events_rebalance.py)
- [tests/test_search_events_integration.py](../../../tests/test_search_events_integration.py)

### 14 · Conditional metrics, bounded I/O and alerts

**Scope:** Core; optional monitoring requires configuration.

[Diagram source](14-metrics-and-alerts.mmd) · [Vector preview](14-metrics-and-alerts.svg)

Metrics are best effort and never establish processing success. Conditional writes merge different fields but the same field remains last-successful-writer. Alerts describe supplied rules; delivery is not configured by generating this board. The earlier prose mentioning eight alerts is older than the current fourteen-rule file.

Implementation:

- [app/services/metrics_service.py](../../../app/services/metrics_service.py)
- [app/services/storage_service.py](../../../app/services/storage_service.py)
- [app/db/session.py](../../../app/db/session.py)
- [app/workers/maintenance.py](../../../app/workers/maintenance.py)
- [ops/alerts.yml](../../../ops/alerts.yml)
- [ops/prometheus.yml](../../../ops/prometheus.yml)
- [ops/alertmanager.yml](../../../ops/alertmanager.yml)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_conditional_storage.py](../../../tests/test_conditional_storage.py)
- [tests/test_operational_safety.py](../../../tests/test_operational_safety.py)

### 15 · Verified backups and operator recovery

**Scope:** OPTIONAL · operations profile.

[Diagram source](15-backup-and-disaster-recovery.mmd) · [Vector preview](15-backup-and-disaster-recovery.svg)

These are implemented backup checks and a documented operator recovery procedure, not evidence of a completed cloud disaster-recovery drill. Six-hour logical snapshots do not provide point-in-time recovery. Restore commands are not run while creating this diagram.

Implementation:

- [ops/backup.py](../../../ops/backup.py)
- [docs/production_operations.md](../../../docs/production_operations.md)
- [Dockerfile](../../../Dockerfile)
- [docker-compose.yml](../../../docker-compose.yml)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_backup.py](../../../tests/test_backup.py)
- [tests/backup_restore_smoke.py](../../../tests/backup_restore_smoke.py)

### 16 · Legacy compatibility and guarantee boundaries

**Scope:** Legacy and implementation limits.

[Diagram source](16-legacy-and-boundaries.mmd) · [Vector preview](16-legacy-and-boundaries.svg)

The legacy whole-video handler remains for old messages; new uploads use the durable pipeline. No unverified benchmark result is promoted to a passed gate. Kafka remains a locally validated prototype with outstanding acceptance gates, as recorded in the validation report.

Implementation:

- [app/workers/task.py](../../../app/workers/task.py)
- [app/db/video_repository.py](../../../app/db/video_repository.py)
- [docs/reliability_and_recovery.md](../../../docs/reliability_and_recovery.md)
- [docs/kafka_validation_results.md](../../../docs/kafka_validation_results.md)

Existing evidence/test definitions (not executed by this documentation task):

- [tests/test_video_repository_retry_state.py](../../../tests/test_video_repository_retry_state.py)
- [tests/test_pipeline_migration.py](../../../tests/test_pipeline_migration.py)

## Import and validation

Whimsical supports `flowchart TB`, native editable nodes, subgraphs, and inline style classes. See [its Mermaid import documentation](https://whimsical.com/learn/boards/mermaid). The browser editing connection was unavailable in this chat, so these files have not been inserted into the remote board.

Run `python docs/diagrams/reliability-map/build_map.py` to rebuild the sources, then `node docs/diagrams/reliability-map/render_map.cjs` to parse every diagram and create the local visual guide. The renderer downloads the version-pinned Mermaid browser bundle once into this task's temporary directory.

For the current Kafka acceptance-gate evidence, see [the validation report](../../kafka_validation_results.md). This map does not reevaluate those gates. The latest handoff records no active benchmark; no benchmark or monitor was started for this documentation task.
