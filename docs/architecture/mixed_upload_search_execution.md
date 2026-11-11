# Mixed upload/search experiment execution

PR: https://github.com/DavidQifongJiang/Vindex/pull/23

## Completed isolated run

- Identity: `vindex-pipeline-bench-4f55401319`; account `585572506964`, region `us-east-1`.
- Server: `i-00f743c0331f2df95`, m7i.2xlarge; client: `i-092c99c08456b3d53`, c7i.2xlarge.
- Bucket: `vindex-pipeline-bench-585572506964-4f55401319`.
- Local private journal/evidence: `.review-cache/mixed-experiments/run-001/` (ignored; never commit credentials).
- Started 2026-11-11 around 17:21 UTC. Both bootstrap processes completed around 17:24 UTC. All owned cloud resources were removed and their absence verified at 21:36 UTC, before either automatic stop deadline.
- Shared state host remains running. Shared desired counts remain API=1, search worker=0, playback worker=0. No shared application configuration is changed.
- Initial harness commit: `f1336a327c06f67a9f8964a6782507938493d2f2`. Measured application commit remains `a458bc2e5b19335025eed8ec63599e2c02297330`.
- Final stage: all 8 calibration, 24 primary and 4 worker-active trials completed; evidence preserved and reproduced; cloud cleanup verified. See the [results report](mixed_upload_search_results_2026-11-11.md).

The first calibration had two trials, API then direct: 12 upload arrivals/minute, 2 semantic queries/second, 16 prepared upload contexts, 60 seconds baseline + 90 seconds load + 30 seconds recovery, 180-second upload deadline, 25,808,821-byte speech fixture. New-video processing was paused after verifying the three protected seed videos.

The server ran `mixed_campaign.py`; the client independently ran `mixed_client.py`. Their control channel was the owned bucket. Each trial was archived before teardown, with progress under `evidence/<campaign>/progress.json`. Campaigns ran independently of the desktop connection. The final archive was downloaded and hash-verified before resource deletion. No background Kafka benchmark was part of this experiment.

All measurement-validity and integrity gates passed. Search latency gates failed for the API route at higher loads, and the worker-active full-pipeline sustainability gate failed at 36 uploads/minute on both routes. Those failures are retained, with no changes to sample counts or acceptance thresholds. No cloud benchmark or experiment polling monitor remains active.

## Calibration completed

Eight exploratory trials finished with 396/396 accepted, byte-verified uploads and 2,880/2,880 correct semantic responses. All arrival ledgers, client headroom, infrastructure stability and integrity checks passed. These observations are excluded from the primary estimate.

| Offered uploads/minute | API search p95 | Direct search p95 | API / direct relative-latency gate |
|---:|---:|---:|---|
| 12 | 185 ms | 180 ms | passed / passed |
| 24 | 217 ms | 175 ms | failed / passed |
| 36 | 239 ms | 167 ms | failed / passed |
| 60 | 315 ms | 165 ms | failed / passed |

Each pilot had a 60-second no-upload baseline, 90-second load interval and 30-second recovery. All absolute p95 values remained below one second. API search p95 at 60/minute was 1.764 times its own baseline, versus 1.003 for direct S3. This is measured interference, not evidence of an outage. The 12/36 pilots used 16 upload slots; the 24/60 pilots used 24. All primary trials use the same 24-slot pool.

## Primary registration

Registered at 2026-11-11 18:07:07 UTC, before dispatch. Plan SHA-256: `eecbd7473370a188f7d92f5dee028a7786b149c89232ac8f980aa071ebffa8c9`.

The [exact plan](evidence/mixed_upload_search_2026-11-11/primary-plan.json) contains 24 trials: four rates (12, 24, 36, 60/minute), two routes, three paired repetitions. Each uses a 60-second baseline, 180-second load, 30-second recovery, fixed 2 queries/second and the 25,808,821-byte speech fixture. Planned totals: 2,376 uploads and 12,960 queries. Rate order rotates between repetitions and route order alternates. Sample counts and gates will not change in response to primary outcomes.

Runtime sources were hash-verified between calibration and primary collection. The added verifier checks completed new search publications outside timed intervals; it does not change application behavior. Both hosts' clocks were within 30 microseconds of AWS time at the recorded preflight check. Separate hard-stop timers at 22:15 UTC back up the graceful expiry timers.

Primary collection ran from 18:08 to 20:46 UTC, followed by the registered worker-active confirmation. Estimated EC2 compute through the stop request was $3.19; this is not a bill and excludes small storage, request, IPv4 and shutdown charges. The original $5 estimated round ceiling and five-hour host lifetime were unchanged.

## Worker-active confirmation registration

The [worker-active plan](evidence/mixed_upload_search_2026-11-11/integrated-plan.json) was registered before primary outcomes were available. It contains one paired repetition at 3 and 36 uploads/minute, with 300-second load intervals, the same 2 queries/second, and fixed search/playback workers. Planned totals: 390 uploads and 3,360 queries. It is an exploratory check of actual completion rates and backlog, not a second population capacity estimate. Completed search publications received vector-token/cardinality and semantic-response checks outside the timed interval. Unfinished jobs were preserved and explicitly retired during isolated teardown.

The confirmation completed all four trials between 20:46 and 21:29 UTC. All 390 uploads were accepted and verified; all 3,360 scheduled queries returned the expected result. Both routes drained the 3/minute workload by the final recovery observation. At 36/minute, both had 137 pending videos near load end and growing backlog/age during the last half of load. Direct uploads improved existing-video search latency while the fixed processing workers remained the end-to-end limit.

## Final evidence and validation

- Primary: 2,376/2,376 accepted, byte-verified uploads; 12,959/12,960 correct queries, including 8,640/8,640 during load. One recovery query received HTTP 502 when the benchmark proxy's API connection reset; its exact cause remains unproven. The failure is retained in the [investigation](evidence/mixed_upload_search_2026-11-11/failure-investigation.json).
- All 24 primary and four confirmation trials passed generator timing/headroom, infrastructure and integrity validation. Confirmation worker quotas and container stability matched the registered plan; no processing jobs failed before explicit teardown retirement.
- The final capture verified runtime/frontend hashes against registration, unchanged protected-seed artifact lineage, and twelve additional semantic checks outside timed trials. It then stopped writers and removed the synthetic corpus.
- The final private archive contains 1,004 verified files. The public archive contains 704 selected measurement/configuration/verification files, each checked against the preserved source manifest. Private SQL snapshots, credentials and raw application logs remain local.
- Both published numeric reports were rebuilt from a fresh extraction of the public archive and compared as complete JSON objects. See the [reproduction receipt](evidence/mixed_upload_search_2026-11-11/reproduction.json).
- Ten Python analysis/protected-corpus regression tests and thirteen JavaScript measurement tests passed. Full CI passed for the harness and offline helpers at `b6be065e49a4d8e11c9e72b6e7aebe34cebc3f27` ([run 85](https://github.com/DavidQifongJiang/Vindex/actions/runs/43085048995)); the final artifact commit's checks are tracked on PR #23.
- Both instances and volumes, the bucket, security group, IAM role and instance profile are absent. Shared ECS counts and the existing state host are unchanged, and both shared health endpoints returned HTTP 200. See the [cleanup receipt](evidence/mixed_upload_search_2026-11-11/cleanup-verification.json).
