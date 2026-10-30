# Phase 2: durable search indexing

Implemented locally, September 16, 2026. Activation remains disabled by default.
This phase provides indexing infrastructure; the library HTTP endpoint and UI
remain phases 3 and 4. No relevance or latency improvement has been demonstrated.

## Publication and recovery

Postgres records accepted transcript revisions, immutable generation settings,
projection jobs, processing attempts, and deletion tombstones. The upload finalizer
commits the accepted revision and indexing intent with its successful completion.
A bounded maintenance scan also registers existing completed videos.

The dispatcher reserves work in Postgres before publishing Celery messages to the
`indexing` queue. Dispatch is repeated after its reservation expires, so a broker
outage or lost notification does not erase work. Delivery is at least once.
Workers claim a five-minute renewable lease with a thirty-minute absolute deadline.
Each attempt writes distinct physical IDs; complete content verification precedes
guarded publication of that attempt in Postgres. A stale attempt cannot publish.
Retries use persisted backoff and stop after three attempts. Malformed source,
mapping, or model data requires explicit repair.

Elasticsearch and Qdrant have independent publication state. Indexing failure does
not invalidate the uploaded video. Revision changes and deletion revoke search
eligibility immediately in Postgres. Physical cleanup waits until the attempt's
deadline plus one hour, then revisits old attempts hourly to catch late writes.
Attempt and tombstone records are retained; production retention and compaction
need a separate policy before sustained high-volume operation.

Maintenance verifies ready projections and queues repair when content is missing.
Transient verification outages preserve publication state; detected source
corruption revokes it. Elasticsearch deletion refreshes before selecting documents,
preventing the earlier delete-by-query version conflict after a repair.

## Local activation procedure

1. Back up the target database, review migration `20260916_0006`, and run
   `alembic upgrade head` before starting updated application processes.
2. Configure `ELASTICSEARCH_URL` and a fresh `SEARCH_LEXICAL_TARGET`. In Compose,
   use `http://elasticsearch:9200`. Keep experimental per-video indexes separate.
3. Decide semantic generation settings before first activation. An empty
   `SEARCH_EMBEDDING_REVISION` creates only the lexical generation. To create both,
   set a full immutable model commit, `EMBEDDING_MODEL`, `SEARCH_VECTOR_SIZE`, and
   a fresh `SEARCH_SEMANTIC_TARGET`. The embedding loader uses that revision;
   artifacts record the actual loaded model commit. Legacy embedding artifacts
   without model provenance fail closed and need regeneration. Do not relabel
   old vectors to make validation pass.
4. Set `SEARCH_PROJECTIONS_ENABLED=true` consistently on API, processing workers,
   dispatcher, maintenance, and indexing worker. Start the Compose `search`
   profile with the existing dispatcher and maintenance services. The dedicated
   indexing worker consumes `indexing`; existing worker queues do not.
5. Inspect projection states and maintenance metrics before using the indexes.
   Initial configuration is persisted once. Changing environment variables does
   not switch an active generation; generation cutover requires an explicit
   reviewed migration/configuration procedure.

For a failed job, correct the cause and run:

```sh
python -m app.workers.search_indexing --repair JOB_UUID
```

The dispatcher delivers that persisted repair request. To stop using this path,
disable the flag consistently and restart affected processes. Retain the new
tables and indexes while investigating; rollback does not require deleting data.
Do not disable the flag as a substitute for waiting for in-flight work to finish.

## Validation evidence and limits

- Full regression run: 174 tests, 173 passed, one skipped. Real PostgreSQL and
  Elasticsearch test connections were configured. The skipped test requires Redis
  for the existing pipeline's real Celery delivery drill.
- Two additional upload/finalizer/deletion tests passed on SQLite and PostgreSQL.
- PostgreSQL tests use isolated schemas and cover concurrent claims, process death,
  lease recovery, retry exhaustion, revision/deletion races, and migration
  upgrade/downgrade with ORM column agreement.
- Elasticsearch tests use disposable named indexes and cover publication payloads,
  attempt isolation, repair, and deletion. Qdrant tests use its in-memory client
  with real vector normalization, identity, and payload behavior.
- Broker redispatch is tested with a mocked broker. A real Redis/Celery delivery
  drill for this new task, remote Qdrant testing, and container hard-timeout testing
  remain staging validation tasks; the tests do not establish their success.
- Source storage and media/embedding generation are replaced in automated upload
  tests. No live S3 ingestion or model download was performed for this phase.

Local logs: `tmp/phase2-full.log`, `tmp/phase2-finalizer.log`, and
`tmp/phase2-integration.log`. These are local evidence, not benchmark artifacts.
Production migration, activation, and phase 0 quality baselines remain outstanding.
