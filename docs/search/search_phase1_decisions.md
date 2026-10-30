# Phase 1 completion: concrete implementation decisions

This is the design output of the user's instruction to proceed with phase 1.
It complements [the full contracts](search_phase1_contracts.md) and distinguishes
design completion from runtime readiness. Subsequent coding authorization delivered
the [phase 1 contracts and fusion integration](search_implementation_spec.md).
No new production dependencies, database migrations or index writes were made.

## Decisions

| Choice | Design | Reason |
| --- | --- | --- |
| Library membership | Signed-in user's uploads only | Explicit user choice; preserves existing public-video access separately |
| Partial indexing | Return available current results with an indexing notice | Explicit user choice; one unfinished upload must not block older videos |
| Source of truth | Postgres ownership, current revision and per-engine readiness; S3 immutable content | Search indexes remain rebuildable and cannot grant access |
| Index layout | Shared Elasticsearch passage index per schema generation | Supports a library query without creating a shard for each video |
| Passage identity | Stable UUID per video, accepted revision and ordinal | Prevents cross-video fusion collisions; repeat processing preserves logical identity |
| Physical write identity | Index attempt ID plus passage ID | Late/expired attempts cannot overwrite a replacement's content |
| Background mechanism | Postgres projection jobs dispatched through existing Celery/Redis, with periodic reconciliation | Durable retry, fencing and independent readiness without a new broker |
| Search selection | Filter by authorized, accepted projection IDs before top-k and validate results again | Obsolete/private output cannot fill or leak through the result list |
| Migration | New Qdrant collection, existing saved embeddings where safely mapped | Keeps old per-video search available; avoids unnecessary transcription/model work |
| First lexical policy | Transcript-only standard analysis/BM25 baseline | Avoids changing passage boundaries, title weighting and analyzer behavior simultaneously |

The engineering choices in this table are the phase 1 design, not claims that the
user individually selected each internal mechanism. Product choices remain as
confirmed in conversation. Phase 0 quality and performance evidence is still needed.

## Concrete state ownership

`search_revisions` records an accepted source manifest once. Its immutable fields
are revision ID, video ID, S3 reference, exact-byte checksum, canonical passage
schema version/count and source provenance. `videos.current_search_revision_id`
selects currentness; UUID ordering has no meaning. Register existing accepted paths
under a uniqueness constraint before changing the current pointer.

`search_projections` has a unique key of `(video_id, revision_id, backend,
generation_id)`. It contains durable scheduling and publication state: pending,
indexing, retrying, ready, failed or obsolete; attempt number/limit; lease/deadline;
next retry; claim token; accepted projection ID/count/checksum; and safe error code.

Use a separate attempt record to retain attempt ID, physical target and lifecycle
timestamps for cleanup. It survives projection replacement long enough for late
writes to be collected. Do not cascade-delete the only cleanup record with a video.
Record durable delete intent with the video identity and targets before cleanup.

`search_configuration` selects the enabled generation pair. Each generation stores
its backend target and compatible source schema, plus model name/revision/dimension
for dense retrieval or analyzer/mapping revision for lexical retrieval. Requests
capture one configuration version; workers publish into their explicit target.

Names describe the migration contract, not tables created in phase 1. Phase 2 must
implement real PostgreSQL constraints and locking tests, not rely on SQLite to
establish concurrent ownership guarantees.

## Query execution contract

1. Resolve signed-in owner and current video/revision membership from Postgres.
2. Select ready accepted projections for each requested engine in the captured
   active configuration. Readiness is a persisted publication decision, not a
   claim that an engine can never lose data later.
3. Query each nonempty eligible set concurrently, including query embedding in
   the semantic branch budget. A branch without eligible videos is `not_ready`.
4. When both branches execute successfully, fuse canonical passage ranks. On
   failure, use the surviving ranking and set `degraded=true`. Index coverage
   and execution failure are independent fields.
5. Revalidate returned video/revision/projection tuples and authoritative access.
   Drop obsolete hits, perform only bounded refill, and return current metadata.

An external index may be lost after Postgres marks it ready. Detect a missing
physical target as an unavailable branch; reconciliation revokes affected readiness
and schedules repair. Silent document loss is not necessarily detected by a query;
the integrity scan's interval and verification strength must be documented. Never
claim that Postgres publication alone guarantees perpetual index completeness.

No network request should hold a video row lock. Publication uses short guarded
transactions. Authority failure is a request failure, not permission to return
unchecked results from a healthy engine.

## API and user-visible state matrix

`degraded` specifically means a requested execution branch failed/timed out.
`coverage.complete` describes index eligibility at the request's scope snapshot.
`effective_mode` identifies successfully used retrieval modes, not the reason for
missing coverage. Keep result ordering from the surviving engine during fallback.

| Situation | HTTP | Effective mode | Degraded | Coverage | User message |
| --- | --- | --- | --- | --- | --- |
| Empty owned library | 200 | none | false | complete | No videos yet |
| Both engines ready and successful | 200 | hybrid | false | complete | No warning |
| Nine videos ready; tenth not indexed | 200 | hybrid | false | incomplete | 1 video is still indexing |
| All videos semantic-ready; some lack lexical indexing | 200 | hybrid if both execute | false | incomplete | Keyword indexing is still in progress for some videos |
| Only semantic has any eligible videos | 200 | semantic | false | incomplete | Keyword indexing is still in progress |
| Both ready; semantic execution fails | 200 | lexical | true | complete | Showing keyword results; semantic search is temporarily unavailable |
| Lexical succeeds with zero hits; semantic fails | 200 | lexical | true | as observed | No keyword matches; semantic search is temporarily unavailable |
| Nonempty library; no engine has any eligible video | 409 | none | false | incomplete | Videos are still processing or indexing |
| Both executable engines fail | 503 | none | true | as observed | Search is temporarily unavailable |
| One engine fails; the other has no eligible video | 503 | none | true | incomplete | Search is temporarily unavailable; indexing is also in progress |
| Authoritative access validation unavailable | 503 | none | n/a | unknown | Search is temporarily unavailable; return no candidates |

Pending and terminal indexing failure should have separate counts/reasons: do not
tell a user a failed job is merely “still indexing.” Likewise, distinguish video
processing from indexing based on authoritative state. Responses use safe codes;
stack traces, transcript paths and internal hosts stay out of user messages.

Numeric timeouts, eligibility cap, candidate limits and refill limit are phase 3
settings informed by measurement. They must be finite and tested before enablement.
This is an explicit later deliverable, not a promise of unlimited library size.

## Stale work, deletion and rebuild examples

**Worker A expires; worker B succeeds.** A and B have different physical attempt
IDs. Postgres accepts B under its current fencing token. A can leave orphaned
documents but cannot publish them or change B's documents. Search filters B's ID.
The cleanup scan discovers A through retained attempt records and removes it after
the grace policy. An RPC timeout is not proof that A stopped running.

**A video is deleted while indexing.** The deletion transaction marks it deleting,
revokes eligibility and persists cleanup intent. Publication rejects both old and
new claims. Search's final access check excludes the video. Retry physical deletes
with visibility/version-conflict handling; retain the tombstone/attempt inventory
through the worker deadline and GC grace. No exactly-once cleanup claim is made.

**An analyzer or embedding model changes.** Build new backend generations from
the same accepted source, validate coverage/quality, then update the selected
generation pair in Postgres. No blind rollback to an obsolete transcript revision.
Old storage survives the reviewable rollback window; deletion applies to both old
and new targets. Redis loss does not erase rebuild intent held in Postgres.

## Handling the unfinished prototype

Do not enable the existing local Elasticsearch edits as this design's implementation.
Phase 2 needs to reconcile them explicitly:

- Keep the experimental per-video benchmark client separate.
- Replace marker-only eligibility with authoritative projection publication.
- Replace path-only runtime identities with canonical revision/passage identities.
- Add attempt-isolated physical writes and persisted retry/cleanup state.
- Fix and retain the failing refresh/deletion test as regression evidence.
- Preserve application behavior until migrations/backfill and feature gates are ready.

The provisional maintenance scanner can inform reconciliation, but is not the sole
owner of publication or the only record of per-video failures.

## Completion checklist

- [x] User scope and partial-coverage policy recorded.
- [x] Logical/physical passage identities and legacy mapping defined.
- [x] Per-engine readiness, query failure and empty-result semantics separated.
- [x] Pre-retrieval filtering and final authority checks specified.
- [x] Index topology and durable background mechanism chosen for the design.
- [x] Publication, stale-worker, deletion and migration contracts described.
- [x] API examples/state matrix and phase 2 test inventory supplied.
- [ ] Phase 0 baseline measurements (separate outstanding work).
- [ ] Phase 2 runtime implementation and real-service concurrency tests.
- [ ] Performance gates, frontend journey and rollout validation in later phases.

Phase 1 design and domain-contract code are complete. Focused correctness tests do
not establish distributed pipeline correctness, improved relevance, latency gains
or production readiness. See the implementation specification for remaining work.
