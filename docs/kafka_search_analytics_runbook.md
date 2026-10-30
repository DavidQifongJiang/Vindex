# Optional Kafka search analytics

This is a local development deployment, not a highly available production cluster.
Kafka supports independently consumed search analytics and review projections;
Celery/Redis still transports video-processing commands. Postgres owns durable state.
See `kafka_search_analytics_plan.md` for acceptance gates and
`kafka_implementation_handoff.md` for the active run and outstanding work.

## Start an isolated test environment

Install the application dependencies and `requirements-analytics.txt` in Python3.11.
Start `docker compose -p vindex-analytics -f docker-compose.analytics.yml up -d --wait`.
The volume initialization service sets ownership of the Kafka data directory.
Postgres is available only on localhost15434, Kafka on localhost19092.
The compose credentials are disposable local test credentials.

Set `VINDEX_TEST_DATABASE_URL` to the compose Postgres URL and
`VINDEX_TEST_KAFKA_BOOTSTRAP=127.0.0.1:19092`, then run:

```powershell
python -m unittest tests.test_search_events tests.test_search_event_worker tests.test_search_events_integration -v
```

Integration tests create/drop uniquely named schemas and topics. They must never
point to production. The CI Kafka job provisions these services explicitly; an
ordinary test run without these environment variables skips live integration.

## Application setup

Use Alembic to migrate the chosen application database before enabling analytics.
Alembic uses `DATABASE_URL` (or the ordinary `PG*` application settings), not the
`SEARCH_EVENTS_*` variables. For the normal public-schema deployment, set
`DATABASE_URL` to the application Postgres URL, leave `SEARCH_EVENTS_SCHEMA=public`,
and run `python -m alembic upgrade head`. Point `SEARCH_EVENTS_DATABASE_URL` to that
same database, or omit it to inherit the application URL. If using a pre-created
dedicated schema for a complete isolated fixture, set `PGOPTIONS=-c search_path=<schema>`
for migration and the application, and `SEARCH_EVENTS_SCHEMA=<schema>` for event
workers; these settings must refer to the same schema. The schema must contain
the authoritative video/revision/deletion tables too: review authorization reads
them directly. A separate analytics-only database is insufficient for real-user
review authorization with this implementation.

The migration adds contexts, consent withdrawal markers, outbox/budget, projection,
receipt, quarantine and checkpoint tables. `create_all` does not migrate a deployment.
Set `SEARCH_EVENTS_DATABASE_URL` and optional `SEARCH_EVENTS_SCHEMA` consistently
for the API and workers. Set `SEARCH_EVENTS_KAFKA_BOOTSTRAP` and
`SEARCH_EVENTS_TOPIC` for publisher/consumers. Bootstrap the topic explicitly:

```powershell
python -m app.workers.search_events bootstrap
python -m app.workers.search_events publish
python -m app.workers.search_events consume --generation analytics-v1
python -m app.workers.search_events consume --generation review-v1
python -m app.workers.search_events maintain
```

Each long-running command needs its own supervised process. Do not launch multiple
copies accidentally. Capture, interaction intake, and dashboard flags default off.
Enable only the intended flags from `.env.example`; configure the explicit reviewer
allowlist. Use authenticated user traffic or the clearly labeled synthetic mode.
Query sharing requires separate consent. Raw query text is not put in Kafka.

## Delivery and recovery

The producer claims outbox rows with expiring tokens and marks publication only
after acknowledgment. A crash between acknowledgment and marking can republish a
record. Consumers deduplicate event IDs within a projection generation. Batches
hold at most50 records and collect for at most100ms, then commit receipts,
projections, quarantine and database offsets atomically. Kafka next offsets are
committed afterward, individually for each partition. This is at-least-once
delivery with idempotent database effects, not exactly-once execution.

An assignment change discards an uncommitted buffer so its new owner can reread
it. A database or offset-commit failure restarts the consumer loop. Durable
checkpoints reject missing/expired inconsistent history; do not set a permissive
offset reset policy to hide this failure. Repair or rebuild deliberately into a
shadow generation after understanding the retained range.

Capture is bounded and optional. A timeout can still commit later; distinguish
committed, failed, busy, timeout-unknown and returned interaction capability.
Investigate counter-lock contention and database availability if these worsen.
Never interpret a false interaction capability as proof that no event was stored.

## Replay and quarantine

```powershell
python -m app.workers.search_events replay --output tmp/replay-new.json
python -m app.workers.search_events replay --manifest tmp/replay-new.json --output tmp/replay-confirmation.json
```

Use a fresh output filename. The saved manifest fixes partition start/exclusive-end
offsets. Replay produces a new `replay-<uuid>` generation, preserves live offsets,
and records projection and handler hashes. New arrivals beyond the manifest are
excluded. Expired ranges fail explicitly. Inspect equal hashes, counts, quarantine
and authorized review visibility; a checksum alone does not prove correct semantics.
Replay never replaces the live dashboard in this phase.

Quarantine retains location and digest, not arbitrary raw bytes. Correct handler
bugs and replay unchanged retained bytes into a fresh generation. Corrected payloads
are new events; do not mutate Kafka history. Bad-input repair drills remain a gate.
After confirming a shadow is inactive, retire it using `retire --generation replay-<uuid>`.
Do not delete active-generation receipts: their lifetime protects deduplication.
Active-generation rotation and storage growth require an explicit lifecycle policy.

## Benchmarks and monitoring

Set `SEARCH_EVENTS_DATABASE_URL` to the isolated analytics Postgres URL. Use a new
output directory for every run:

```powershell
python -m benchmarks.search_event_stream --seconds 60 --rate 100 --output tmp/kafka-screen-new
python -m benchmarks.search_event_stream --seconds 1800 --rate 100 --output tmp/kafka-sustained-new
```

The harness creates its own schema/topic and retains evidence. It measures both
groups' receipt counts and post-database-commit freshness, captures Docker CPU,
memory and block I/O, and records schema disk use. Docker read permission is needed
for resource samples. Event timing logs are synthetic benchmark instrumentation,
enabled only by `SEARCH_EVENTS_MEASURE_FRESHNESS=true`.

A report can exist before replay completes: require `finished_at` and a successful
process exit. Check failure fields, attempted versus accepted counts, both group
counts, committed freshness observations/p95, replay equality and resource errors.
Freshness alone must not pass on a partial processed subset. Admission throughput
is not consumer throughput. The current workload is synthetic completions; mixed
interactions and actual search capture have separate tests.

Run `benchmarks.search_events_overhead --output tmp/kafka-overhead-new` with the
isolated quality Postgres/Qdrant/Elasticsearch test URLs after sustained load has
finished. It uses50 lecture captions,500 queries,three paired rounds and four
clients. Do not run another load test concurrently unless that contention is the
explicit experiment. Compare capture capability with durable context count,
including the20 warmup captures; do not conflate the two.

Follow `AGENTS.md` to schedule a task heartbeat near the estimated finish and retry
one minute later if incomplete. Pause the monitor on completion/failure and record
the outcome. Do not stop the broker to run outage drills while another benchmark
is using it. Never weaken gates to make a failed run pass.

## Shutdown and limits

Stop worker processes first, then stop the isolated compose project. Preserve
volumes and evidence when investigating failure. `down -v` destroys the local
broker/database test data; use only for deliberately disposable CI fixtures.
Single broker, replication1, minimum ISR1 and PLAINTEXT are development settings.
Seven-day time retention and1GiB byte retention per partition may trim history
earlier than seven days under high volume. Expired outbox events are counted;
expiry is not successful delivery. No availability or production-scale claim is
supported by this configuration.
