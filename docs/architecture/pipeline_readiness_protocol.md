# Upload-to-search and playback evaluation

Status: instrumentation and protocol prepared; no integrated cloud campaign has
been run with this harness. The completed [600-upload comparison](upload_comparison_results_2026-11-10.md)
measured durable acceptance only. Its results do not establish processing speed.
This phase keeps the current processing DAG unchanged and compares upload routes.

## Measurement contract

One monotonic client clock starts immediately before the Upload button action.
It includes browser hashing, transfer, completion, queueing, processing and probes.

| Milestone | Required observation |
| --- | --- |
| Accepted | Successful upload/completion JSON with a video ID, using the existing upload measurement |
| Searchable | A real per-video `qdrant_embedding` query returns the expected speech phrase in the annotated time interval for that video |
| Playable | The processed file URL loads in a browser video element and a presented frame advances to at least 0.1 seconds with nonzero dimensions |

Search and playback probes start independently after acceptance. They do not
wait on each other or pass based on database status flags. A nonempty unrelated
search result, wrong video, wrong time interval, HTTP 200 containing corrupt media,
or an audio-only file does not establish readiness. This measures per-video semantic
search, not optional library-wide indexing, lexical search, or search relevance broadly.

Probes run inside the same browser page and inherit its network emulation. They
use the same-origin API and current Vindex login session; the storage request
does not receive API authorization headers. Credentials, signed URLs, transcripts
and raw response bodies stay out of the measurement artifacts. Use synthetic or
licensed test speech; the declared query/expected phrase are recorded in `run.json`.

The result is **observed readiness**, an upper bound on when the user could have
succeeded. It includes probe request/response time, playback startup, and polling
delay. The proposed pilot polls one second after each unsuccessful probe; the
maximum observation delay is not simply one second when probes take time. Every
probe records start/finish offsets. Keep the interval and query constant between
modes. Probes add API/model work and media reads; record this overhead separately
from upload-only CPU results. Existing frontend background requests remain enabled
and identical in both builds. This is a readiness probe, not a full click-through
search/player interaction or a rendering-latency benchmark.

Report click-to-accepted/searchable/playable and acceptance-to-readiness separately.
Never use server `accepted_at_epoch` or its derived processing latency as a common
origin: the legacy handler and direct intent start at different points.
The global deadline starts at the upload click, not after acceptance. A success
observed after it is censored. Each probe has an abort deadline and a driver
watchdog with 250 ms cleanup allowance; cleanup time cannot turn a late response
into a pass. Upload errors, timeouts and setup failures stay in all denominators.
Successful branch observations survive a failure in the other branch.

## Pilot and comparison design

1. Prepare a 20-second speech video with an independently checked transcript,
   expected phrase and time interval. Record SHA256, bytes, codec, duration,
   resolution and audio channels. Use the exact same bytes in both modes.
   The earlier upload-only synthetic corpus does not validate transcription.
2. Run one paired cold trial, then one paired warm trial: four measured uploads,
   concurrency one. Alternate the route order between pairs (API/direct, then
   direct/API). This is a functionality/instrumentation pilot, not a statistical
   comparison. Register warm-ups separately; never pool them into the sample.
3. For process-cold trials, restart both workers before **each mode**, with the
   same image/model files already cached, empty queues, and no prior processing
   in those worker processes. Record worker identities and startup/model-load logs.
   Service provisioning from zero is excluded; a later scale-from-zero experiment
   needs a separate cohort and lifecycle evidence.
4. For warm trials, process an excluded speech fixture in both worker queues,
   verify search/playback, then drain all work. Keep those processes alive for
   the pair. Keep API query-model state consistently warm in both cold/warm
   cohorts; otherwise worker cold start is confounded with query-model loading.
5. Hold application image, DAG flags, Whisper/embedding versions, CPU/thread limits,
   worker counts, dispatch settings, API/proxy capacity, browser version and
   network profile fixed. Proposed starting capacity follows repository defaults:
   API 0.5 vCPU/2 GiB; one search worker 2 vCPU/4 GiB; one playback worker
   1 vCPU/2 GiB; concurrency one per worker. These are proposed settings, not a
   report of observed cloud allocation. Pin actual images/settings in the run record.
6. After the pilot, preregister the primary sample size, absolute staging SLO,
   duration/size mix and budget before collecting primary data. Include longer
   speech videos and five concurrent uploads only after the small pilot passes.
   Preserve cold/warm cohorts and paired waves in analysis; do not pool clients
   as independent samples. At least 100 successful observations per mode/cell
   are needed before presenting p95 as more than exploratory, and even that does
   not prove a population reliability target.

Proposed comparison gate: direct/API p95 upper paired-wave 95% confidence bound
at most 1.10 for both search and playback in each matched cell; zero integrity or
duplicate-publication failures; observed full-readiness rate at least 99%, with
uncertainty and every failure reported. The absolute staging SLO is **not yet
agreed**. These gates and sample size must be locked before the primary campaign;
four successful pilot uploads cannot pass them. The existing upload-only analyzer
does not analyze these readiness records; a primary campaign needs its own
validated paired-wave analysis before results can be accepted.

## Isolation, budget and preflight

Use an isolated API/dispatcher/workers, PostgreSQL database, Redis queues, Qdrant
collection and media bucket. Use a test identity and ingress supporting the
legacy upload sizes. Do not point this pilot at the shared deployment merely
because a manifest says staging. The former upload-only EC2 hosts/bucket have
been deleted, and its wrapper intentionally has no processing services.
Provisioning the isolated integrated stack is a separate execution prerequisite.

Before provisioning, record a price estimate for the chosen services, dollar cap,
resource expiry and cleanup owner. The initial execution proposal is four measured
uploads plus explicitly counted warm-ups, a 600-second deadline per attempt and
60-minute total resource lifetime. Stop on the first failed wave; capture evidence
before expiry. If warm-up/setup consumes the budget, stop rather than extending
the run implicitly. The browser harness bounds one wave to five clients and a
maximum 900-second attempt deadline; **it does not enforce cloud lifetime or spend**.
No cloud resources are created by the instrumentation PR.

Immediately before each wave, record operator-observed isolation, empty durable
job queues and Redis queues, worker state, fixed capacity, models, and cleanup
plan. Save this private JSON outside version control:

```json
{
  "isolated": true,
  "queue_empty": true,
  "observed_at": "REPLACE_WITH_CURRENT_UTC_TIMESTAMP",
  "commit": "REPLACE_WITH_FULL_MEASURED_APPLICATION_COMMIT",
  "target": "https://isolated.staging.example.invalid/bench-direct",
  "worker_state": "cold",
  "media_bucket": "DEDICATED_TEST_BUCKET",
  "database": "DEDICATED_DATABASE_IDENTITY_NO_PASSWORD",
  "redis": "DEDICATED_REDIS_AND_QUEUE_IDENTITIES_NO_PASSWORD",
  "qdrant": "DEDICATED_COLLECTION",
  "worker_identity": "PROCESS_OR_TASK_IDS_AND_BOOT_TIMES",
  "capacity": "OBSERVED_CPU_MEMORY_COUNTS_AND_THREAD_LIMITS",
  "model_versions": "PINNED_WHISPER_AND_EMBEDDING_MODELS",
  "cleanup_plan": "RESOURCE_JOURNAL_AND_PRESERVATION_PLAN_LOCATION"
}
```

The runner checks required fields, freshness within 15 minutes and identity
agreement, and records the file hash. This is an operator attestation, **not
independent verification of isolation, queue depth or worker temperature**.
Archive a sanitized preflight and the supporting observations before publishing
results; do not infer environment correctness from the hash or a string label.
The runner does not restart/warm workers, drain queues, or verify database/S3 state.

## Running and retaining evidence

Copy `backend/benchmarks/pipeline_pilot.example.json` to a private location, replace
the fixture annotation/paths, measured commit, same-origin frontend URLs, test
session and preflight. API/direct builds must use the same application commit.
Each invocation runs **one mode, one fixture, one wave** so the operator can reset
workers between cold trials. The frontend/API must share an origin; a separate
`VITE_API_BASE_URL` deployment is not supported by these readiness probes.

From `backend/`, using Node 22 and the frontend Playwright installation:

```text
node benchmarks/run_upload_comparison.cjs --manifest C:/private/pipeline-pilot.json --output ../.review-cache/pipeline-cold-direct-001
```

`run.json` records the measurement contract, declared worker state, preflight hash
and browser/runtime versions. `fixtures.jsonl` records input hashes.
`milestones.jsonl` checkpoints acceptance/video identity immediately, then each
probe. `attempts.jsonl` retains completed attempts and both branch outcomes;
`waves.jsonl` and `summary.json` include all-attempt readiness denominators and
explicitly exploratory successful-only quantiles. `completion.json` distinguishes
finished work from interruption and separates acceptance from full readiness.
Missing completion/attempt records are an interrupted run, not successful uploads
that may be dropped. Preserve them separately and do not analyze as a complete pair.

The runner leaves created videos/artifacts/jobs for reconciliation. It rejects
`benchmark_control`: that upload-only endpoint expects pending initial jobs and
would remove evidence needed by processing workers. After every wave, record raw
object checksum/size, reservation/video identity, durable job attempts/statuses,
active leases, published artifact tokens and Qdrant publication identity. Exactly
two initial jobs is not the total processing-job count when chunking is enabled.
Verify expected outputs and absence of duplicate logical publications before
draining/stopping workers and performing reference-aware cleanup.

For failed/interrupted work, preserve PostgreSQL/job snapshots, logs, raw/provisional
objects, manifests and checksums **before** automatic host expiry. Stop only the
isolated stack; never delete objects while a worker may hold a valid lease. After
successful reconciliation use the application's deletion/GC path, confirm jobs,
S3 objects and vectors are gone, then remove only journaled test resources.
Publish a cleanup receipt and remaining resources, even if the experiment fails.

## Subsequent gates

After the paired pipeline baseline, run the remaining upload matrix cases
(450 MiB, ten clients and the 5 Mbps/100 ms profile) within a separately bounded
budget. Run recovery experiments separately: interrupted transfer, lost completion
acknowledgement, concurrent completion, expired/replayed URL, database/broker outage,
and reference-aware orphan collection. Preserve lease/token and attempt-publication
contracts. Rollout checksum enforcement and any later DAG optimization each need
their own reviewed PR, rollback path and evidence. None is silently enabled by
this measurement change.
