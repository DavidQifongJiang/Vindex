# Mixed upload/search experiment execution

PR: https://github.com/DavidQifongJiang/Vindex/pull/23

## Current isolated run

- Identity: `vindex-pipeline-bench-4f55401319`; account `585572506964`, region `us-east-1`.
- Server: `i-00f743c0331f2df95`, m7i.2xlarge; client: `i-092c99c08456b3d53`, c7i.2xlarge.
- Bucket: `vindex-pipeline-bench-585572506964-4f55401319`.
- Local private journal/evidence: `.review-cache/mixed-experiments/run-001/` (ignored; never commit credentials).
- Started 2026-11-11 around 17:21 UTC. Both bootstrap processes completed around 17:24 UTC. Automatic stop timers are set around 22:08 UTC, within five hours of launch; planned completion and cleanup target is earlier.
- Shared state host remains running. Shared desired counts remain API=1, search worker=0, playback worker=0. No shared application configuration is changed.
- Initial harness commit: `f1336a327c06f67a9f8964a6782507938493d2f2`. Measured application commit remains `a458bc2e5b19335025eed8ec63599e2c02297330`.
- Current stage: calibration complete; primary comparison registered before collection. No primary capacity claim yet.

The first calibration has two trials, API then direct: 12 upload arrivals/minute, 2 semantic queries/second, 16 prepared upload contexts, 60 seconds baseline + 90 seconds load + 30 seconds recovery, 180-second upload deadline, 25,808,821-byte speech fixture. New-video processing is paused after verifying the three protected seed videos.

The server runs `mixed_campaign.py`; the client independently runs `mixed_client.py`. Their control channel is the owned bucket. Each trial is archived before teardown; progress appears under `evidence/<campaign>/progress.json`. Local desktop disconnection does not stop a dispatched campaign. The archive is downloaded and hash-verified before resource deletion. No background Kafka benchmark is part of this experiment.

Remaining gates: validate client timing/headroom and corpus correctness; freeze primary rates/repetitions; complete paired comparison; repeat selected rates with workers active; analyze product outcomes and limits; preserve final evidence; remove both hosts and associated resources; finish PR checks.

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

Expected primary duration is about 2.5 hours, followed by worker-active confirmation and final evidence capture. The original $5 estimated ceiling and five-hour host lifetime remain unchanged.

## Worker-active confirmation registration

The [worker-active plan](evidence/mixed_upload_search_2026-11-11/integrated-plan.json) is registered before primary outcomes are available. It contains one paired repetition at 3 and 36 uploads/minute, with 300-second load intervals, the same 2 queries/second, and fixed search/playback workers. Planned totals: 390 uploads and 3,360 queries. It is an exploratory check of actual completion rates and backlog, not a second population capacity estimate. Completed search publications receive vector-token/cardinality and semantic-response checks outside the timed interval. Unfinished jobs are preserved and explicitly retired during isolated teardown.
