# Durable pipeline and recovery

New uploads use Postgres-backed task intent, leased claims, and attempt-specific
outputs. Redis remains the Celery transport. The dispatcher is required for new
uploads to progress; the API no longer publishes tasks directly to Redis.

## Durable work and publication

- `pipeline_jobs` contains one row per `(video_id, stage)` for `search`
  preparation, `playback`, and `finalize`.
- `video_search_chunks` contains the durable intent and state for each chunk.
- Upload inserts the video and its search/playback jobs in one transaction.
- Preparation commits its completed state and all chunk rows together.
- Chunk completion commits the accepted artifact token and, when the last chunk
  completes, a single finalization job in the same transaction.
- A retry changes the durable row to `retrying` with `next_retry_at`; it does not
  rely on publishing a Celery retry message in a separate step.

These job rows serve as the transactional outbox. The standalone
`python -m app.workers.pipeline_dispatcher` process polls Postgres, reserves a
dispatch window, and publishes eligible work. It republishes work that remains
unclaimed after `PIPELINE_REDISPATCH_SECONDS`, including after broker data loss,
an ambiguous publication result, or a dispatcher crash. Delivery is at least once;
the database claim decides whether a delivery can execute.

## Ownership and failure handling

Every claim increments the persisted attempt count, creates a UUID token, and
sets a lease expiry and a non-renewable deadline. A heartbeat renews only the
lease every one-third of its duration. Explicit checkpoints also record progress.
All publishing, completion, and failure transitions verify the current token and
lease. Parent video locks serialize claims, publication, finalizer creation, and
the deletion decision. Database transactions are not held across media/model work.

The dispatcher recovers expired leases with bounded backoff and marks exhausted
work failed. A returning obsolete worker cannot renew its expired lease or update
its replacement. The persisted attempt count is authoritative even if a new
Celery message starts with `request.retries == 0`.

S3 output keys include the attempt token. Qdrant IDs and payloads include the
chunk attempt token. Search filters to the tokens accepted in completed chunk
rows, so late writes and extra vectors left by an earlier transcription stay
invisible. Existing completed chunks without tokens remain readable through a
restricted legacy filter for those chunk indexes.

Finalization has independent ownership, retries, and terminal failure state. It
publishes merged artifact paths and completion in one transaction. Overall video
readiness is committed with playback/finalization completion, avoiding a crash
window after the final job completes. Deletion also checks active jobs/chunks,
sets `deleting` before cleanup, and removes all attempt-prefix artifacts present
at cleanup time. A failed cleanup can be retried through the delete endpoint.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `PROCESSING_LEASE_SECONDS` | 300 | Lease length; minimum 30 seconds |
| `PIPELINE_DISPATCH_INTERVAL_SECONDS` | 5 | Dispatcher polling interval |
| `PIPELINE_REDISPATCH_SECONDS` | 60 | Repeat publication if work stays unclaimed |
| `MAX_PROCESSING_ATTEMPTS` | 3 | Attempt limit persisted when work is created |
| `PROCESSING_DEADLINE_SECONDS` | 1800 | Absolute deadline per claimed attempt |
| `MEDIA_COMMAND_TIMEOUT_SECONDS` | 900 | Timeout per FFmpeg/ffprobe invocation |
| `UPLOAD_DEADLINE_SECONDS` | 1800 | Maximum lifetime of an upload reservation |
| `ARTIFACT_GC_GRACE_SECONDS` | 86400 | Minimum orphan artifact age before deletion |
| `MAINTENANCE_INTERVAL_SECONDS` | 60 | Cleanup, index repair, dependency check interval |

Recovery latency includes lease expiry, polling, and retry backoff. Broker
visibility timeout is no longer the only way abandoned work becomes runnable.
Monitor dispatcher health, overdue pending/retrying work, expired leases,
failed jobs, and database/broker connectivity.

## Rollout

1. Pause uploads and stop/drain old API and worker processes before migration.
   Old workers cannot participate in the new token protocol.
2. Back up Postgres and apply `alembic upgrade head` using the new code.
   Revision `20260904_0004` adds the job table, chunk token/lease fields, and
   `videos.thumbnail_path`. It preserves completed content, creates jobs for
   active videos, and makes previously processing chunks eligible for recovery.
   Revision `20260905_0005` adds upload reservations, operational state, and
   deadlines. It expires in-flight attempts from the previous revision.
3. Build and start the updated API, search worker, playback worker,
   `pipeline-dispatcher`, and `maintenance` Compose services. Workers use Linux
   prefork so Celery soft/hard time limits take effect. Check logs and run a small upload.
4. Verify chunk claims, finalization, playback, and search, then resume uploads.

`CREATE_TABLES_ON_STARTUP` does not alter existing tables; run the migration.
Queued legacy whole-video messages cannot claim videos with durable jobs.
The old whole-video handler remains for compatibility, but new uploads use the
durable pipeline. Schema downgrade is tested; reverting application versions
after new processing also requires a data/artifact compatibility plan.

## Verification

Run `python -m unittest discover -s tests -v` for local tests. Set
`VINDEX_TEST_DATABASE_URL` to a dedicated Postgres test database to enable real
locking, subprocess-crash recovery, and migration tests. Tests create and drop
their own uniquely named schemas. Set `VINDEX_TEST_REDIS_URL` to a dedicated Redis
test instance to additionally exercise the production Celery task wrapper and
duplicate message delivery with a cheap test processor.

The service-path test covers upload through deletion using in-memory S3 media
artifacts, stubbed FFmpeg/models, and Qdrant's local implementation. It does not
benchmark real model throughput or exercise AWS S3 outages.

## Upload retries

The frontend keeps an `Idempotency-Key` for retries of the same selected file,
title, and visibility. Clients outside the frontend must retain and resend their
own key. Keys are scoped to the authenticated owner and checked against a
streamed SHA-256 fingerprint of the file and metadata. A changed payload returns
409; an in-progress duplicate returns 409 with Retry-After. A committed retry
replays the original response without uploading or creating jobs again.

An expired reservation keeps its video ID but receives a new attempt token and
raw S3 key. Only the current token can commit the response, video, and initial
jobs in one transaction. Failed commits release the reservation when Postgres is
available; otherwise expiry permits recovery. Deleted uploads retain a tombstone
and return 410 for the old key. Requests without a key remain backward compatible
but do not have cross-request deduplication. Page reloads/new file selections are
new frontend upload intents, not content-addressed deduplication.

## Operational recovery

The maintenance service scans bounded S3/Qdrant pages with persisted cursors. It
preserves recorded artifact paths, active upload/work tokens, and accepted chunk
vectors; it removes old unreferenced attempts, including late writes after video
deletion. Unknown S3 path formats are left alone. A failed deletion remains
`deleting` and can be retried by its owner. Cleanup cannot recover deleted data.

Missing accepted Qdrant vectors are rebuilt from S3 embedding manifests. Repair
is count-based, not a check of every vector's contents. Per-video repair errors
are recorded without blocking the rest of the scan. This does not automatically
restore missing S3 objects or regenerate failed chunks.

S3 metrics use ETag conditional writes with bounded conflict retries so concurrent
updates to different fields do not overwrite one another. The same metric field
still has last-successful-writer semantics. Metrics remain best effort and log
failures; they never determine whether processing succeeded.

Prometheus scrapes the internal maintenance endpoint, and eight alert rules cover
dispatcher inactivity, runnable queue age, expired/stalled work, terminal failures,
dependencies, maintenance, backups, and monitoring availability. The supplied
Alertmanager receiver is deliberately local-only: configure a real destination
and test delivery before deployment. These are pipeline alerts, not a complete
search API latency/error-rate dashboard or an external host availability monitor.

The optional `operations` Compose profile runs a six-hourly Postgres backup. It
requires a separate `BACKUP_S3_BUCKET` and versioning enabled on both buckets.
Success is recorded only after upload, checksum-verified download, and a real
restore into a unique disposable database. The backup identity needs CREATEDB
for the drill; do not grant that privilege to the API identity. See
[production operations](production_operations.md) for configuration and recovery.

## Remaining limits

- This is not a distributed transaction or exactly-once execution. Expired
  workers may still write isolated, unreferenced artifacts. Periodic garbage
  collection handles these eventually; there is no cross-system rollback.
- Tune stage deadlines using real video durations and CPU/model benchmarks. The
  defaults can reject legitimate long jobs. Time-limit enforcement in production
  depends on prefork; Windows/solo tests exercise fencing, not OS-level killing.
- Backups and alerts require external configuration and permissions. Local tests
  do not establish AWS durability, notification delivery, or disaster recovery
  time. Logical snapshots do not provide point-in-time recovery between backups.
- Keep hosts time-synchronized. Leases use UTC application timestamps. Run an
  external monitor: local monitoring cannot notify during complete host loss.
- Pause workers and maintenance during restoration. Restore referenced S3 object
  versions before restarting cleanup; versioning alone is not an independent
  backup against account compromise or deletion of old versions.
- The retained `celery_enqueue_seconds` upload metric now measures the database
  transaction recording task intent; `enqueue_backend` identifies this as
  `postgres_outbox`. Queue transport timing should be measured at dispatch.
