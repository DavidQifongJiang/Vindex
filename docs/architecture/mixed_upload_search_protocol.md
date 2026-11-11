# Fixed-capacity upload and search comparison

Status: calibration completed; [primary plan](evidence/mixed_upload_search_2026-11-11/primary-plan.json) registered before collection. No primary capacity result yet.

## Question and scope

Can Vindex admit more uploads while existing videos remain searchable at the same API allocation? Compare the deployed application's API upload route with its direct-to-S3 route. This is an isolated controlled experiment, not evidence of historical production incidents. Application commit `a458bc2e5b19335025eed8ec63599e2c02297330` and its existing pinned image are unchanged. The only frontend difference is the upload-mode build setting.

API allocation stays at 0.5 vCPU and 2 GiB. Dedicated server: m7i.2xlarge. Dedicated client: c7i.2xlarge. Both use on-demand, non-burstable capacity in us-east-1. New bucket, database, Redis, Qdrant, credentials, IAM role and security group belong only to this run. No shared application service is scaled or replaced.

## Workload and denominators

- Three real, processed synthetic speech videos form the protected search corpus. Search uses the actual semantic endpoint and checks both video identity and the expected transcript interval.
- The production React upload form runs in a bounded pool of browser contexts. Each upload client has 20 Mbps upload, 100 Mbps download and 40 ms latency. Queries use a separate Node process and CPU quota on the client host; query-network conditions are identical across routes.
- Both upload and query starts follow absolute scheduled arrivals. Slow responses never reduce offered traffic. Every scheduled arrival, timeout, unavailable slot and late response stays in the ledger.
- Each trial has a no-upload search baseline, a mixed-load interval, and recovery. Search-only latency is measured with the same prepared browser tabs, warm query model and fixed query rate.
- Preserve bytes/SHA-256, unique logical video IDs, upload reservation and durable initial-job accounting. Preserve database snapshots and failed trials before bounded teardown. Teardown of paused or unfinished test jobs is explicitly labeled retirement, never processing success.

## Calibration and locked comparison

Calibration is exploratory and excluded from the primary estimate. Start at 2 searches/second, 16 upload slots, and the 25.8 MB fixture, exploring 12, 24 and 36 upload arrivals/minute. The 103.2 MB fixture can test whether a conclusion depends on object size. If the search-only baseline itself exceeds the threshold, reduce the search rate before locking the primary protocol, document why, and retain the invalid pilot.

After calibration, publish and hash an exact primary plan before collecting its observations: fixture(s), search rate, upload rates, durations, repetition count and alternating API/direct order. Prefer at least three independent paired repetitions near the observed capacity boundary. Lock sample counts based on calibration and remaining budget, never primary results. Preserve all primary failures. If no boundary is reached, report only the tested lower bound. Short trials do not establish long-running production capacity.

Run two separately reported stages:

1. New-video workers paused: measure upload admission's interference with already-processed-video search. This does not establish processing throughput.
2. Fixed workers active: repeat selected rates, measuring completed search/playback work and backlog count/age. Growing backlog prevents a sustainable end-to-end capacity claim even when admission and existing-video search pass.

## Predefined gates

| Gate | Requirement |
|---|---|
| Search responsiveness | Mixed-load p95 <= 1 second AND <= 1.20 times its matched no-upload baseline |
| Search availability/correctness | >=99% observed successful expected results; timeouts/errors remain failures |
| Upload acceptance | >=99% of scheduled arrivals accepted; retain late/unreturned commits separately |
| Integrity | Every accepted identity present exactly once; original bytes/hash and initial durable jobs verified |
| Generator validity | Complete unique arrival ledger; no pool exhaustion; p99 scheduling lag <=100 ms; no client OOM/restart; peak observed client CPU quota and memory utilization each <90% |
| Full-pipeline sustainability | Worker backlog count and oldest age do not continuously grow at the claimed rate; report actual completion rates separately |

Report per-trial values and paired estimates with uncertainty; do not pool all requests as independent experiment repetitions. A 99% observed pass is not a 99% reliability guarantee. Preserve baseline and failure latency distributions, not successful requests alone. Investigate server/proxy/client constraints before assigning a bottleneck to API CPU.

The primary report will compare the arithmetic mean of per-trial p95 latencies, with a paired 10,000-resample percentile bootstrap (seed 20261111). Resample whole API/direct trial pairs, never individual requests. Report every repetition and its gate outcome; classify a tested rate as meeting the target only when every valid planned repetition passes all admission/search/integrity gates. Any invalid repetition prevents that classification. No interpolation between tested rates establishes an unmeasured capacity maximum. With only a few trial pairs, intervals describe this experiment and do not establish a population SLA.

## Cost, reversibility and evidence

Estimated ceiling: $5 for this round; both machines automatically stop within five hours. Reserve time for evidence capture and cleanup. AWS Price List API verified compute rates: server $0.4032/hour, client $0.357/hour, combined $0.7602/hour. Estimates exclude small storage, request and IPv4 charges; they are not an AWS bill. Stop earlier if the forecast threatens the ceiling.

All harness and report changes go through a PR. No application deployment is required. Before deleting cloud resources, stop writers, archive snapshots and logs, download and verify the evidence manifest, and preserve or verify every residual object. Terminate both instances and their volumes, then remove their bucket, security group, role and profile. Confirm shared services remain unchanged.

CPU utilization is explanatory evidence. Provisioned-cost savings remain unverified unless a separate smaller-API-capacity experiment meets the same gates. No claims of lower bills, fewer crashes, or faster time-to-searchable follow from CPU reduction alone.
