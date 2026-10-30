# Phase 3: library retrieval API

Implemented locally on September 16, 2026. `LIBRARY_SEARCH_ENABLED=false` remains
the default. This phase adds the API; the library interface is phase 4. Existing
within-video search is unchanged. See [indexing operations](search_phase2_operations.md)
for the prerequisite migration, generations, and published projections.

## Request and response

Authenticated `POST /search`:

```json
{"query":"Dijkstra Fibonacci heap","scope":"my_library","mode":"hybrid","limit":5}
```

Only the authenticated owner's non-deleting uploads participate. Other people's
public videos remain accessible through existing individual-video routes. Owner
IDs and unknown fields are rejected. Query length is 1–500 characters, limit is
1–20, and modes are hybrid (default), semantic, and lexical. At most 100 videos
participate; larger libraries receive 422 `library_scope_too_large` without silent
truncation. This is a release bound, not a demonstrated scaling limit.

Responses include `request_id` (also `X-Request-ID`), `requested_mode`,
`effective_mode`, `degraded`, `coverage`, `branches`, `notices`, and `results`.
Results include video/revision/passage IDs, ordinal, current title, text,
`start_seconds`, `end_seconds`, and `matched_by`. Internal scores are not exposed
as confidence values. Obtain playback through the existing authorized video route.

| Situation | HTTP / behavior |
| --- | --- |
| Successful search, including zero matches or empty library | 200 |
| One requested engine fails or times out | 200 from the surviving engine, explicit degraded status |
| Some current projections missing | Available results and `library_indexing_incomplete` |
| No eligible projections in a nonempty library | 409, with processing or indexing-failure notices |
| Every executable engine fails | 503 |
| Initial or final authority lookup fails; overall deadline expires | 503, no unchecked text |
| Per-process admission exhausted | 429 and `Retry-After: 1` |
| Endpoint disabled | 404 |

`coverage` describes eligibility, independently of engine execution. Successful
empty search is different from an unavailable engine. Single-engine modes never
silently invoke the other engine. Feature-disabled behavior and authentication
follow the application's existing authentication configuration, including its
explicit local development user mode.

## Execution and correctness

Each scope lookup uses a fresh database transaction; PostgreSQL uses a read-only
repeatable-read snapshot. Capture active generations and only ready projections
for the current accepted source path/revision. Each backend receives an OR of
exact `(video, revision, published attempt)` tuples before top-k retrieval.
Missing indexes are errors, not empty successes. Reads never create indexes.

Lexical retrieval and semantic embedding/retrieval run concurrently. Semantic
encoding happens once and must match the published generation's model commit and
dimension. Elasticsearch uses standard text matching. Both fetch at most 50
candidates. Canonical passage identity joins the rankings with equal-weight RRF,
k=60. Ties among returned candidates use passage ID; fallback preserves the
surviving ranking. No phrase boosts, synonyms, fuzzy matching, or title search
were added without evaluation.

Re-read authority before response assembly. Drop hits whose ownership, deletion
state, accepted revision, published attempt, or active generation changed. Current
titles come from this final database read. New publications are included on the
next request. Return `search_scope_changed` when the scope or eligibility changed.
This implementation returns fewer results after removal; it uses zero of the
design's optional one refill allowance. It does not introduce another embedding
call or extend the deadline to fill a page.

The HTTP deadline is 3 seconds; initial scope resolution has a 500 ms budget,
branches have at most 2 seconds including embedding, and final validation uses
the remaining budget. PostgreSQL statement and transport timeouts are bounded.
There are no application search retries. Eight requests per API process are
admitted. Each has at most two branch workers. A response can return promptly with
a surviving branch while timed-out work still occupies its admission slot until
it finishes. Thus timeouts cannot create an unbounded background queue. These
limits are per process, not deployment-wide capacity guarantees.

Python threads cannot forcibly cancel a stuck model call, socket resolver, or
database connection acquisition. The HTTP deadline still holds; sustained stuck
work can exhaust admission and needs operational recovery. Warm the pinned model
and validate remote connection behavior before rollout. Logs record completion
metadata and correlation IDs without query/transcript content. Full tracing,
dashboards, load testing, and performance promotion remain phase 5 work.

## Validation and activation

The complete integration-enabled regression run passed 197 tests, with one
existing Redis-dependent Celery delivery test skipped (198 discovered). Subsequent
targeted tests cover the final response fields, semantic model compatibility, and
transport timeout classification; see `tmp/phase3-final-targeted.log`.

Coverage includes parallel branch execution, degradation, successful empty results,
missing/failed indexing, mode isolation, overload, retained admission during a
timeout, strict API validation, disabled/authenticated access, and fail-closed final
validation. Real PostgreSQL tests include ownership transfer during retrieval,
scope limits, and accepted-source selection. Real Elasticsearch and in-memory
Qdrant tests verify exact tuple filtering and obsolete-attempt exclusion.

Remote Qdrant, a real embedding-model download/inference, live S3 ingestion,
deployment-wide saturation, and browser journeys were not validated in this phase.
No latency or relevance improvement is claimed. PostgreSQL tests used a disposable
container/schema; Elasticsearch tests created and removed uniquely named indexes.

For a reviewed local environment, complete phase 2 setup and set
`LIBRARY_SEARCH_ENABLED=true` on the API process, then restart it. Turning this
flag off disables the library endpoint without stopping indexing or changing
within-video search. Production activation remains a separate rollout decision.
