# Search implementation specification and phase 1 code handoff

Current status: phases 1–4 are implemented; phase 5 local comparisons, failure/load
drills, and observability are delivered. [The evaluation](search_phase5_evaluation.md)
recommends holding rollout for independent relevance and deployment-specific
validation. Earlier phase handoff sections below are retained as history.

The user authorized completing the design and coding phase 1. This document closes
the remaining design choices with explicit starting settings. Settings are bounded
engineering defaults, not measured performance guarantees. Baseline evaluation may
justify revisions; it is no longer necessary to reopen design simply to begin the
next implementation block.

See [contracts](search_phase1_contracts.md), [decisions](search_phase1_decisions.md)
and [roadmap](elasticsearch_search_roadmap.md). They define the architecture and
confirmed product behavior. This document governs the code/design completion status
where earlier text says runtime implementation was paused.

## Release behavior and starting settings

| Area | Implementation specification |
| --- | --- |
| Library scope | Authenticated user's uploads; public videos owned by others remain accessible individually but excluded from My library |
| Default mode | Hybrid for the new library endpoint; preserve the existing within-video default until that endpoint is migrated and evaluated |
| UI | Search my videos; result title, contextual text and timestamp; click opens playback at that timestamp; retain Search this video |
| Engine controls | Semantic/lexical/hybrid modes remain available for API evaluation; ordinary library UI uses the selected default rather than showing infrastructure names |
| Query input | Trim outer whitespace; 1–500 characters; result limit 1–20, default 5; reject unsupported scope/mode |
| First-page behavior | Bounded top results, no pagination in this release; deterministic tie order by logical passage ID |
| Candidate retrieval | Start at 50 candidates per branch; RRF k=60, equal weights; at most one refill of 50 per branch under remaining deadline |
| Passage grouping | No new merging/rechunking or adjacent-hit suppression in the first baseline; logical-ID deduplication only, to isolate retrieval changes |
| Scope bound | Initial maximum 100 owned non-deleting videos per library request; fail with 422 `library_scope_too_large`, never silently truncate; validate the cap before rollout |
| Request budget | Initial 3,000 ms wall-clock deadline; up to 500 ms scope resolution, up to 2,000 ms branch work including embedding, reserve remaining time for validation/response; all phases use the same monotonic absolute deadline |
| Branch retries | No synchronous dependency retries in search requests; recovery belongs to later requests and background work |
| Overload | Initial per-API-process cap of 8 active library searches; reject excess with 429 and Retry-After rather than creating an unbounded work queue; deployment-wide capacity is not implied |
| Lexical index | Versioned shared index, transcript text, standard analyzer, BM25; no custom synonyms, fuzzy defaults or phrase boosts until evaluated |
| Failure UX | Show available results; distinguish backend failure, incomplete indexing, processing and terminal indexing failure using safe response codes |
| Rollout | Library endpoint behind a disabled-by-default feature flag until authorization, lifecycle, failure and relevance gates pass |

The phase 1 query contract implements input bounds. Phase 3 will enforce request
budgets/admission/candidate/scope caps in the orchestrator; they are not enforced by
the current API. Configure and benchmark them together, preserving a finite ceiling.
Network timeouts must be no greater than remaining branch budget. A canceled caller
does not necessarily stop blocking work: bounded executors retain their occupied
slot until the work ends, preventing hidden accumulation.

Coverage is scoped to requested modes. An `ok` branch with zero hits still counts
as successful. If none of a nonempty library is ready, return 409; distinguish
terminal indexing failure from processing in the notice. If the only executable
branch fails, return 503. If scope/access validation fails, return 503 without
returning unchecked candidates. A nonexistent/unauthorized single video remains 404.

## Background work configuration

Use separate durable search projection records and a Celery queue `indexing` so
backfills do not starve the current transcription queue. Start with one indexing
worker slot, bulk batches of 250 passages and a reconciliation page of 10 videos.
These are local starting settings, not workload capacity claims.

Reuse the existing bounded backoff, lease and absolute-deadline behavior, with
projection-specific configuration when long documents need it. Retry transient
network/429/5xx failures; classify malformed documents/mapping incompatibility as
terminal rather than looping. Start with three persisted attempts, explicitly
requeue terminal work after repair, and record attempts/errors in authoritative state.

Postgres publication follows verified backend writes and search visibility. Use
projection-attempt-specific physical IDs. No direct read-side index creation and
no indexing in a user's search request. Reconciliation verifies existing published
content and schedules missing work; it does not silently create a second readiness
authority. Preserve attempt/deletion records until deadlines plus GC grace have
passed and repeated cleanup verifies absence.

Local Elasticsearch may use the existing isolated 9.3.1 experiment for disposable
tests. Production must use reviewed authentication/TLS/resource/replica settings;
the unauthenticated single-node Compose configuration is not production approval.

## Identity and legacy conversion details

- Passage namespace: `1a3feaf4-27c1-54d7-b16c-c053c7c465c0`.
- Canonicalize UUIDs to lowercase standard strings. Logical UUIDv5 input is compact
  JSON `[video_id, revision_id, ordinal]`. No text-based cross-video identity.
- Physical namespace is UUIDv5 of `physical-projection-v1` under the passage
  namespace. Physical UUIDv5 input is compact JSON `[projection_id, passage_id]`;
  both storage engines accept a UUID-form physical ID.
- Verify SHA-256 of the exact accepted manifest bytes before parsing; reject
  duplicate JSON keys, non-finite timestamps and malformed/missing fields.
- Preserve manifest order. Do not resort independently for each backend. Convert
  legacy numeric seconds using decimal half-up rounding to integer milliseconds;
  reject negative/reversed/out-of-range intervals before publication.
- Canonical query hits use exact millisecond precision. Preserve text, including
  its whitespace; no analyzer output replaces the accepted source string.
- Identical text/timestamps at different ordinals remain distinct. The same passage
  ID with inconsistent payloads is a contract violation, never last-writer-wins.
- Legacy unscoped hits remain supported by the old within-video fusion path. Mixed
  canonical and legacy hits are rejected, as are mixed revisions of the same video.
  This avoids silently merging incompatible generations during migration.

## Phase 1 code delivered

| File | Implemented responsibility |
| --- | --- |
| `app/services/search_contracts.py` | Immutable query/passage/snapshot/projection contracts, UUID normalization, logical/physical IDs, exact-byte manifest checks and eligible projection selection |
| `app/services/search_policy.py` | Pure, validated response decision for full/degraded/not-ready/unavailable search; coverage counts and separate processing/failure notices |
| `app/services/hybrid_search.py` | Existing rank fusion now preserves canonical identity, rejects conflicting/mixed revisions, prevents cross-video collisions, and preserves legacy per-video behavior |
| `tests/test_search_contracts.py` | Identity isolation, manifest integrity, fusion conflicts, readiness and complete outcome-policy cases |

`eligible_projections` accepts a mapping already authorized and read from Postgres;
it does not query or grant authorization. `search_outcome` takes measured execution
statuses; it does not issue requests, cancel work or prove an empty branch found no
results in a missing index. These are implemented domain contracts, not an assertion
that the new HTTP endpoint or indexing pipeline is live.

The rank-fusion change is connected to existing runtime callers. The other contracts
are ready for the phase 2/3 adapters and are exercised by focused tests. No placeholder
HTTP endpoints, database tables, background tasks or fake production responses were
added. No new dependency was needed.

## Phase 3 implementation update

Phase 4 has subsequently delivered the opt-in library interface and passage playback
navigation. See [phase 4 UI validation](search_phase4_ui.md). Evaluation and rollout
remain outstanding; neither frontend nor backend flags are enabled by default.

The authenticated `POST /search` endpoint now implements bounded parallel retrieval,
authoritative scope filtering/revalidation, and explicit coverage/degradation.
See [phase 3 API and validation](search_phase3_api.md). The endpoint remains disabled
by default. It returns fewer hits with a scope-change notice instead of exercising
the optional refill allowance. UI and evaluation/rollout remain phases 4 and 5.

## Phase 2 implementation update

Durable indexing, publication fencing, repair, cleanup, and the refresh/deletion
race fix are now implemented. See [phase 2 operations](search_phase2_operations.md)
for activation, validation evidence, and remaining integration limits. The phase 1
validation and phase 2 plan below are historical; phases 4–5 remain outstanding.

## Phase 1 handoff record

Validation of phase 1 code:

- `.venv/Scripts/python.exe -m unittest tests.test_search_contracts -v`:
  26 tests passed.
- `.venv/Scripts/python.exe -m unittest discover -s tests`:
  149 discovered, 113 passed, 36 skipped, zero failures/errors. Service-dependent
  integrations were not enabled for this run; skips do not establish their success.
  Output is retained locally in `tmp/phase1-regression.log`.
- Targeted Python compilation passed; design links/JSON examples and code fences
  validated. Existing within-video fusion tests passed in the regression suite.
- No new real Elasticsearch/Postgres failure drill was run. The earlier deletion/
  refresh failure remains a phase 2 blocker and was not fixed by contract tests.

Phase 2: database migrations/repository, real PostgreSQL fencing tests, dispatcher
and worker adapters, accepted revision registration, authoritative publication,
attempt cleanup, and repair. Fix the existing lexical refresh/deletion race before
promotion. Preserve source/embedding mapping checks during canonical backfill.

Phase 3: authorized library endpoint, concrete backend filter builders, concurrent
execution with bounded resources, final revalidation, budgets/admission and the
response policy adapter. Wire policy notices to actual authoritative state.

Phase 4: library UI and degraded/processing/error journeys. Phase 5: fixed-corpus
held-out comparisons, mixed load/failure tests and rollout. Phase 0 baseline data
is still outstanding; these defaults do not replace it.

Quality promotion gate: improve the predeclared technical-term/phrase slice without
an unacceptable regression in paraphrases or overall relevance. Set numerical
tolerances after the baseline and before challenger tuning. Latency/cost promotion
is a separate decision using end-to-end measurements; hybrid is not assumed faster.
This evidence-dependent gate is deliberate, not a missing architecture decision.
