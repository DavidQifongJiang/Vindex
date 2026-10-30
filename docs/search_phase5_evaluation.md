# Phase 5: measured library search and rollout decision

September 16, 2026. **Decision: keep both feature flags disabled by default.**
The local evaluation is complete; production promotion is not justified yet.
The implementation demonstrates hybrid retrieval and failure handling, but the
available relevance labels are reused and do not adequately judge library-wide
alternatives or unanswerable requests. No deployment was performed.

## What was measured

The frozen corpus contains 2,450 accepted passages across ten 15-minute algorithm
lectures. All 100 existing labeled queries searched the entire owner library in
each mode. The source and label hashes, model commit, candidate count (50), RRF
k (60), randomized mode order, and starting regression gates are recorded in
[`library_evaluation_plan.json`](../benchmarks/library_evaluation_plan.json).
No retrieval parameters were tuned against the new results.

The harness uses the actual FastAPI route, PostgreSQL scope/publication records,
Elasticsearch 9.3.1, Qdrant 1.18.1 over REST, and a cached CPU
all-MiniLM-L6-v2 model pinned to commit
`c9745ed1d9f207416be6d2e6f8de32d1f16199bf`. Data is verified before publication.
Schemas, indexes, and collections use disposable unique names. Source transcripts
and existing application data were not changed.

Latency is an in-process ASGI application round trip with real store connections
and embedding inference. It includes scope queries, embedding, retrieval, fusion,
revalidation, response construction, and telemetry. It excludes external HTTP
ingress/TLS, JWT verification (a fixed benchmark owner is injected), live S3
ingestion, and the production network. Therefore these are not production latency
or deployment capacity figures. The first request is after indexing/model loading,
not a cold application start.

## Quality and latency

Primary run, 100 queries per mode:

| Mode | Hit@1 | Hit@5 | Precision@5 | MRR | Warm p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qdrant semantic | 55% | 85% | 35.2% | 0.6625 | 349 ms |
| Elasticsearch lexical | 58% | 92% | 40.0% | 0.7040 | 76 ms |
| Hybrid | 58% | 90% | 42.4% | 0.7070 | 351 ms |

All 300 requests succeeded with complete coverage. The follow-up again measured
Hit@5 of 85%, 92%, and 90%; p95 was 327, 88, and 326 ms. Hit@1 for lexical/hybrid
was 59% on the follow-up, and MRR was 0.7090/0.7128. Each independent run creates
new revision/attempt UUIDs, which can change tie ordering; the raw responses retain
the identities. These small rank differences are disclosed rather than presenting
the runs as bit-for-bit identical. Freeze accepted identities as well as source
bytes for future exact-order replay comparisons.

Hybrid improved on semantic-only for this fixture, but lexical-only had the best
top-five hit rate. Hybrid did not demonstrate a latency improvement over semantic
search. Elasticsearch's added operational cost must be justified by relevance and
query capabilities, not a claim that running two engines is inherently faster.

The 88 queries labeled `paraphrase` improved from 86.36% to 90.91% Hit@5 with hybrid.
However, many of these queries contain transcript vocabulary. The ten `concept`
queries improved from 70% to 80%; the two `near_exact` queries all hit in each mode.
Two exact queries are insufficient evidence for a terminology advantage.

These metrics use the existing timestamp-overlap/start-tolerance rubric and also
require the labeled video. Unjudged but relevant passages in another lecture count
as misses. Hit@5 is the chance of finding at least one labeled relevant passage,
not recall over an exhaustively judged set. Precision is a proxy under incomplete
judgments. Do not compare these values directly with earlier per-video results.

## Concrete matches to review

- `lecture03-004`, “sorting needs a comparison function for records”: semantic's
  top five returned generic sorting fragments and missed the expected window.
  Elasticsearch ranked “then you have to provide a comparison function” from the
  sorting lecture at 181.64 seconds first. Hybrid recovered it at rank four.
  This is a concrete reason to retain lexical retrieval, and an example where
  equal-weight fusion weakens a strong lexical answer.
- `lecture03-007`, “binary search compares the key to the middle of the array”:
  semantic included the labeled 380-second passage; hybrid's top five omitted it.
  Lexical also found a plainly related middle-element explanation in another
  lecture that the labels do not judge as relevant. This exposes both a ranking
  regression against the fixture and a need for library-wide adjudication.

The initial “Dijkstra Fibonacci heap” example remains illustrative, not an asserted
failure from these labels. The examples above are actual recorded outputs.

## Failure, concurrency, and no-answer behavior

The first run's engine-unreachable and injected slow-semantic cases completed in
approximately 2.04 seconds, within the 3-second request deadline. An unreachable
engine is simulated by redirecting only the benchmark process to an unused local
port; existing services were not stopped. This Windows environment generally
reached the branch timeout rather than returning an immediate refusal.

| Scenario | Observed behavior |
| --- | --- |
| Elasticsearch unreachable | 200, semantic results, explicit degraded state |
| Qdrant unreachable | 200, lexical results, explicit degraded state |
| Both unreachable | 503, no results |
| Semantic branch held for 2.3 seconds | Lexical results returned at the branch deadline; late work retained its slot |
| One current lexical projection pending | 200, lexical coverage 9/10, semantic 10/10, incomplete notice |
| No current projections ready | 409, processing coverage rather than “no matches” |
| Eight admitted requests blocked by injected dependencies | Ninth request received 429; all eight completed after release |
| Recovery after overload | 200, full hybrid response |
| Concurrent writes to unpublished attempts | No unpublished revision appeared in search results |

The follow-up's healthy concurrency-four run completed 32/32 requests, with p95
704 ms and observed throughput 6.15 requests/second. A separate mixed run issued
24 searches while performing eight real write cycles to unpublished attempt
namespaces in both stores: 24/24 succeeded, p95 862 ms, no orphan results. It took
3.60 seconds of wall time and 17.95 API/benchmark-process CPU seconds; that includes
the in-process writer but excludes database/search-server CPU. These short runs
are not saturation capacity estimates, concurrent-user counts, p99 claims, or a
soak test.

Three exploratory out-of-domain queries concerned sourdough, fresco restoration,
and coral bleaching. Semantic and hybrid returned five matches for all three.
Lexical returned zero for two, but five for the fresco query (matching common
vocabulary). They are probes, not independently judged held-out labels. The system
has **no calibrated no-answer rejection**; do not infer relevance from nonempty
output or invent a score threshold from these three examples.

## Resource evidence and limits

The initial Elasticsearch index store statistic was 183,322 bytes for the passage
index, excluding the vector store and broader service overhead. A post-run Docker
snapshot showed approximately 1.325 GiB for the existing shared Elasticsearch
container, 113.3 MiB for isolated Qdrant, and 28.78 MiB for isolated PostgreSQL.
These are post-run snapshots, not load peaks or incremental per-library costs.
Elasticsearch had other local indexes. No cloud cost estimate is supported by
these measurements. Production replicas, retention, growing libraries, and model
worker memory still require deployment-specific sizing.

## Observability delivered

`app/services/search_observability.py` emits JSON events with a request trace ID,
span/parent IDs, duration, and status for scope, embedding/model access, lexical
and semantic branches, Qdrant, fusion, revalidation, and the request. Context is
propagated through both executor layers. Late spans keep the originating trace ID.
Query text, user IDs, video IDs, transcript text, and exception strings are omitted.
This is log-based application tracing, not an OpenTelemetry collector deployment
or instrumentation inside the database/search servers.

`GET /internal/search-metrics` is disabled without `SEARCH_METRICS_TOKEN` and requires
that token as a Bearer credential. It exposes bounded-label request/branch counters,
coverage-incomplete counters, latency/stage histograms, and an active-work gauge.
The gauge remains occupied while timed-out background work is still running.
429 and 503 responses have `X-Request-ID` for correlation too.

Use `ops/search-scrape.example.yml` to add an authenticated scrape after mounting
the token as a secret in Prometheus. Scrape each API process independently; do not
route per-process counters through a load-balanced address. This wiring was not
enabled on the existing running stack. Alert rules for errors, degradation,
saturation, and provisional p95 limits were validated with promtool (14 rules).

Useful PromQL:

```promql
sum by (outcome) (rate(vindex_library_requests_total[5m]))
histogram_quantile(0.95, sum by (le, mode) (rate(vindex_library_request_seconds_bucket[5m])))
sum by (backend, status) (rate(vindex_library_branch_total[5m]))
vindex_library_active
```

Convert captured spans to a Chrome/Perfetto trace document:

```sh
python -m benchmarks.export_search_trace benchmarks/library_phase5_followup.traces.jsonl tmp/library-trace.json
```

The JSONL files are local evidence, not a configured long-term log retention store.
The metrics require a persistent scrape for history; API restarts reset counters.

## Verification and artifacts

- Full integration-enabled regression: **205 discovered, 204 passed, one skipped**.
  The skipped existing Celery test requires Redis; no new broker-delivery success
  is claimed. PostgreSQL crash/fencing/deletion tests and real Elasticsearch tests
  ran; Qdrant REST was exercised by the evaluation harness.
- All nine follow-up failure gates passed. All five provisional regression gates
  passed; those are engineering screening thresholds, not accepted production SLOs.
- Primary data: [library_phase5_results.json](../benchmarks/library_phase5_results.json).
- Additional drills/repeat: [library_phase5_followup.json](../benchmarks/library_phase5_followup.json).
- Each report has `.traces.jsonl` and `.metrics.txt` companions. Logs of the runs
  and final regression are under `tmp/phase5-*.log`.
- Runner: `python -m benchmarks.evaluate_library`; requires explicit test database,
  Qdrant, and Elasticsearch URLs through `VINDEX_TEST_*` environment variables.
  Set `HF_HUB_OFFLINE=1` to use the cached pinned model. The measured runs used
  `OMP_NUM_THREADS=2`. All created data is scoped to disposable names and removed
  after the run.

## Rollout gate and next evidence

Keep library search opt-in and the existing within-video default unchanged.
Before promotion, obtain an independent set of library-wide judgments covering
technical identifiers/phrases, vocabulary-different paraphrases, and no-answer
queries. Define acceptable regressions and abstention behavior before tuning on
development examples; reserve a separate held-out set for the decision. Compare
lexical-only against hybrid rather than assuming hybrid must win.

Then measure deployment-specific mixed ingestion/search load, cold model starts,
resource peaks, and longer recovery/soak behavior; complete the Redis delivery drill
and authenticated ingress test. Review the resulting service/replica budget,
metrics scrape and trace retention, followed by a limited staging activation.
Rollback is to disable the frontend and API library flags while retaining indexes
and revision records. This report recommends holding promotion and does not request
or perform production activation.
