# Direct upload evaluation

## Scope and current evidence

The current main branch already implements direct browser-to-S3 uploads. This
work hardens that implementation and establishes a controlled comparison with
the retained API upload path. Processing remains the same PostgreSQL-owned DAG.
Multipart S3 uploads, new scheduling, and worker scaling changes are separate
experiments; introducing them now would confound the upload comparison.

Before: browser sends video bytes through the API, which fingerprints the file,
writes it to S3, and commits the video plus initial jobs in PostgreSQL.

After: browser hashes the file, requests an authorized upload intent, sends bytes
to S3 using a signed exact-size/checksum policy, and asks the API to complete it.
The API verifies a server-only snapshot and commits the same video and jobs.
The dispatcher and processing workers follow the same path in both cases.

The expected benefit is reduced API bandwidth, temporary-file activity, and
resource contention under concurrent uploads. Lower end-to-end latency is a
hypothesis, not a guaranteed result: hashing, snapshot copy, extra round trips,
client bandwidth, and cold worker startup all contribute to time users observe.

The rollout in PRs 17–19 is deployed and its authenticated functional smoke
passed 23 checks; see [deployment evidence](evidence/direct_upload_deployment_2026-11-10.json).
The completed [600-upload comparison](upload_comparison_results_2026-11-10.md)
found 16.9–18.4% lower p95 latency with five simultaneous uploads, a 3.0% p95
increase for one uploader, and 84.6–95.0% less API CPU work per accepted GiB.
Every upload passed input integrity and initial-job checks. All three cases
passed the predeclared upload-only gates; production reliability and billing
savings remain unverified.

The controlled upload comparison follows the narrower, predeclared
[November 9 protocol and November 10 recovery amendment](upload_comparison_protocol_2026-11-09.md).
The broader load matrix below remains a roadmap, not a list of completed tests.

## Experiment sequence

1. **Correctness gate:** real staging S3 POST/CORS, wrong checksum rejection,
   wrong size rejection, copy checksum retention, completion retries, owner
   isolation, and accepted input integrity. Run local fault tests against isolated
   PostgreSQL for ambiguous commits and overlapping completion. No lost accepted
   inputs or duplicate logical videos/initial jobs are acceptable.
2. **Upload-only pilot:** pause dispatch only in the isolated test environment.
   Alternate API/direct waves on identical API capacity and storage region. Start
   with 25 MiB, one client, two repetitions. Confirm instrumentation and cleanup
   before increasing traffic. Acceptance is the durable API response, not just
   successful S3 transfer. Pending jobs remain until explicitly cleaned up or run.
3. **Load matrix:** valid 25/100/450 MiB videos; 1/5/10 simultaneous clients;
   two fixed upload networks (e.g. 20 Mbps/40 ms and 5 Mbps/100 ms). Use the same
   corpus, account, region, API task count/resources and warmed state for both
   modes. Repeat paired waves in AB/BA order. Each browser's bandwidth is shaped
   independently; this does not emulate clients sharing one last-mile link.
4. **Integrated pipeline:** restore the same dispatcher/workers and process the
   same representative corpus. Measure acceptance, first searchable result, and
   first playable video separately. Record cold-start and warm-worker trials
   separately. Check actual search results and playback artifacts, not only
   HTTP upload success. Keep queue depth and processing settings fixed/comparable.
5. **Failure/recovery:** interrupt a transfer; fail completion after S3 succeeds;
   lose the commit acknowledgement; overlap completion; expire an upload URL;
   temporarily lose database/broker access; replay a signed URL; verify stale
   unreferenced artifacts are collected after the configured grace period.
6. **Rollout:** review evidence, then merge in dependency order and enable strict
   checksum mode after compatible frontend deployment. Use the previous direct
   build for rollback; production API Gateway cannot proxy large videos.

## Measurement contract

| Metric | Definition and collection |
| --- | --- |
| User upload latency | Browser click to valid durable acceptance response, including file hashing; p50/p95 among accepted attempts |
| Acceptance rate | Accepted attempts / all attempts, including setup failures, HTTP errors and timeouts; report Wilson 95% interval |
| Failure duration | Retain elapsed time and failed stage, including timeouts; never silently drop them from results |
| Hash/setup cost | Click to first upload API request; includes hash work and browser overhead, not a pure hashing microbenchmark |
| Transfer/completion | Direct storage POST and completion response measured separately to locate extra cost |
| API resource use | API/proxy CPU seconds, network bytes and temporary-file writes per accepted GiB; collect independently from workers |
| Search/playback latency | Same browser click origin to observed search and playback readiness; record polling interval and both endpoints |
| Recovery | Logical video/job counts, accepted object checksum, retry outcome, orphan age and cleanup result |
| Cost | Same file volume, API runtime, S3 requests/copies, storage, transfer and worker runtime; include failed attempts |

The existing server `accepted_at_epoch` has different starting points in the two
paths: API handler entry (after framework body parsing) versus direct intent
creation. **Do not compare its derived `time_to_searchable_seconds` across upload
modes.** Direct intent time is now stable across URL refreshes and is also exposed
in metrics as `intent_created_at_epoch`. Client measurements are the common
starting point. Metrics written to S3 remain best effort, not durable acceptance.

## Runner

From `backend/`, with Node 22 and the frontend Playwright dependency installed:

```text
node benchmarks/run_upload_comparison.cjs --manifest C:/private/staging-upload.json --output ../.review-cache/upload-pilot-001
```

Copy `benchmarks/upload_comparison.example.json` outside tracked configuration,
provide two frontend builds of the **same commit** with API/direct upload modes,
and use an isolated staging API behind an ingress that supports the legacy file
sizes. An API Gateway rejection is not evidence that direct upload is faster.
Use a test-account Playwright storage-state file if authentication is enabled;
never commit that file. The runner rejects an unlabeled environment, checks the
observed upload mode, caps concurrency at ten, and refuses to overwrite a run
directory. With ordinary staging targets it creates private videos but does not
delete them automatically. The opt-in isolated wrapper verifies every accepted
input and initial jobs, then removes the measured wave's objects and rows.

`attempts.jsonl` preserves every outcome and created video ID. `waves.jsonl`
records wave durations. `summary.json` groups by mode/file/size/concurrency/network,
and reports acceptance intervals and successful latency percentiles. `run.json`
records settings without session contents, signed URLs, or credentials. The
`completion.json` marker distinguishes a finished run from partial evidence; an
absent marker or `finished_all_planned_waves=false` cannot pass acceptance. The
runner measures upload acceptance only by default. The opt-in single-wave
[pipeline pilot](pipeline_readiness_protocol.md) adds observed search/playback
readiness with independent browser probes and a common clock. The isolated wrapper adds API/proxy
resource snapshots and per-wave integrity/cleanup records. Integrated
search/playback performance and production billing remain separate evaluations.

The automated browser fixture exercises the same measurement module with the
production hash worker. Statistical tests ensure failures stay in the denominator
and one successful attempt does not establish 99% reliability.

## Decision gates and current evidence

Lock gates before collecting the comparison; do not lower them after a failure.

| Gate | Proposed pass condition | Current status |
| --- | --- | --- |
| Local recovery and integrity | All targeted tests pass, including real PostgreSQL concurrency | Passed locally; see PR evidence |
| Real AWS storage protocol | Signed POST, checksum/size/key enforcement, copy integrity and browser CORS | Passed on the shared media bucket; operator credentials, not the deployed task role |
| Deployed application flow | Cognito ownership, API completion/replay, durable jobs and accepted input integrity | Passed: 23 deployed smoke checks |
| API upload bodies | At least 99% reduction in API request-body volume | Passed: over 99.999% in all three cases |
| API process write accounting | At least 95% reduction per accepted GiB; distinct from physical-device writes | Passed: direct-mode write_bytes was zero in the measured waves |
| Upload latency | Direct p95 no more than 10% above API within each matched cell | Passed in all three cases, including the paired-wave confidence interval |
| Reliability | Observed acceptance at least 99%, with uncertainty reported; insufficient samples remain inconclusive | 600/600 accepted; population reliability lower bound remains unverified |
| Processing | No functional regression; search/playback latency meets the agreed staging SLO under matched load | Functional smoke passed; matched-load latency unverified |
| Cost and cleanup | Bounded test resources; no leaked test objects/jobs after cleanup | Temporary hosts, disks, bucket and permissions removed; replacement-run compute estimate $0.466; production billing savings unverified |

Ten successful uploads are a smoke test, not a reliability estimate. A stringent
99% lower confidence bound needs hundreds of independent attempts even with no
failures; agree on that volume and its cost before running it. p95 from fewer
than 100 accepted attempts is explicitly exploratory. For latency comparisons,
report paired-wave variability/confidence intervals rather than treating every
simultaneous client as an independent experimental run.

Preserve failed run artifacts. Publish a measured resume claim only after these
gates have completed; do not infer a latency or capacity improvement from topology.

## Live storage evidence: November 9, 2026

[Completed probe report](evidence/direct_upload_s3_2026-11-09.json): all nine storage
checks passed against the configured bucket in us-east-1. A browser uploaded from
a blank document at the application's allowed CloudFront origin. Invalid checksums
returned `BadDigest`; incorrect sizes returned `EntityTooLarge`/`EntityTooSmall`;
changing the signed key returned `AccessDenied`. The server-side copy retained both
the verified checksum and original bytes. All temporary objects were deleted and
the unique test prefix was confirmed empty.

The probe used operator credentials and the application S3Storage methods from
the proposed code. It did not log in to Cognito, invoke upload API endpoints, or
create database jobs. It is correctness evidence, not a throughput or latency run.
No compute resources were started. The existing state host and one API task were
already running; both worker services had zero tasks when inspected. `/health`
returned 200 through both CloudFront and API Gateway.

To repeat, run `benchmarks/verify_direct_upload_s3.py` from backend/ with explicit
`--account`, `--bucket`, `--region`, `--origin`, `--profile`, and a new `--output`
file. Its browser helper requires frontend's Playwright installation and Node;
`--node` may point to a specific Node executable. It refuses buckets with event
notifications or versioning because those require different isolation/cleanup.
It sends only tiny synthetic payloads, not playable test videos. The probe never
prints signed policies or credentials, deletes only its exact generated keys,
and preserves the report even on a failed check.
