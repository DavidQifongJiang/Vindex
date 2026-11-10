# Pipeline experiment execution record

Prepared November 10, 2026. This document records execution choices; measured
results will be added only from completed artifacts.

- Account/region: 585572506964 / us-east-1. The shared API and state host remain
  unchanged; its worker services were at zero during the read-only inventory.
- Dedicated host: m7i.2xlarge, 8 vCPU / 32 GiB. No inbound security-group rules.
  API, database, Redis, Qdrant, dispatcher, workers and browser have separate
  containers and explicit CPU/memory limits. Browser runs on the same host with
  2 vCPU / 4 GiB; this differs from the earlier upload-only dedicated client.
- Measured application: deployed a458bc2e5b19335025eed8ec63599e2c02297330, pinned
  ECR digest. Database/Redis/Qdrant images use digests observed on the state host.
  API and both frontend upload modes use the same application source.
- API 0.5 vCPU / 2 GiB; search 2 vCPU / 4 GiB, playback 1 vCPU / 2 GiB; one process
  in each worker pool. Models are Whisper tiny and all-MiniLM-L6-v2, baked into
  the image. The current DAG remains unchanged.
- Fixture: 20-second 1280x720 H.264/AAC test pattern with synthetic English speech,
  25,808,821 bytes. Annotated phrase: "vector search", first four seconds. SHA256
  is recorded in both the uploaded source manifest and every measured wave.
- Pilot: cold API, cold direct, one excluded warm-up, warm direct, warm API.
  Concurrency one; 20 Mbps upload / 100 Mbps download / 40 ms CDP setting;
  600-second attempt deadline and stop on a failed wave.
- Budget ceiling for this round: $5. AWS Pricing API returned $0.4032 per host hour
  for Linux shared-tenancy m7i.2xlarge in Northern Virginia. EBS, public IPv4,
  requests/storage and any transfer are additional. This is an estimate, not a bill.
  Pilot host stops automatically after 60 minutes, with up to two minutes for
  evidence capture. Shutdown preserves the disk; termination requires a verified
  local archive and residual-object reconciliation. Any primary extension must
  be recorded before that campaign starts and remain within the round budget.
- Auth: synthetic development identity behind a private random cookie; no Cognito
  credentials or production data. The same-origin internal proxy uses an isolated
  self-signed certificate, explicitly recorded in the manifest/preflight.

`provision_pipeline_experiment.py` prepares and journals the resources. Its default
action is explicit: use `prepare` before `create`. `pipeline_experiment/campaign.py`
runs under systemd on the host, checkpoints after every wave, verifies raw bytes,
durable job/chunk completion and vector publication tokens, then uses application
deletion followed by bounded residual cleanup. Failed waves are preserved.

This setup measures browser-observed per-video semantic search and decoded
playback. It excludes real household networks, Cognito/edge overhead, library
indexing, scaling from zero and production billing. Operator-declared environment
fields are accompanied by actual container identities/limits and queue/database
snapshots. Absolute product SLO remains unset; the primary comparison will use
the predeclared relative non-regression gate and report absolute measured times.

References: [measurement protocol](pipeline_readiness_protocol.md),
[AWS on-demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/).

## Primary campaign preregistration

Locked before primary collection on November 10, 2026. The exact order and jitter
are in `backend/benchmarks/pipeline_primary_2026-11-10.json` (SHA256
`dd45f38db15cc3313a485743afc4ba315c38215f9cbf44efe60997891d9c83e4`).
Pilot data are excluded from every primary estimate.

| Cohort | Input | Concurrent clients | Paired waves | Uploads per mode |
| --- | --- | ---: | ---: | ---: |
| Process-cold workers | 20 seconds / 25,808,821 bytes | 1 | 100 | 100 |
| Warm workers | Same 20-second file | 1 | 100 | 100 |
| Warm workers, concurrent uploads | 80.139 seconds / 103,231,087 bytes | 5 | 20 | 100 |

There are 600 measured uploads and six separately counted warm-up uploads.
API/direct order alternates within each cohort. An independently seeded 0–5 second
delay before each measured wave prevents accidental synchronization to the
dispatcher's five-second polling interval. Empty queues and state are verified
before every wave. Workers restart before each process-cold mode; this excludes
service provisioning and uses already cached model files. Each warm cohort starts
with a verified, excluded warm-up covering both processing branches.

The gate is the previously proposed direct/API p95 upper paired-wave 95% bootstrap
bound at most 1.10 for both search and playback in each cell, zero integrity or
duplicate-publication failures, and observed full readiness at least 99%.
Use 10,000 resamples, seed 20261110, nearest-rank quantiles, and keep clients in the
same wave together. A cell with fewer than 100 observations per mode remains
exploratory. The absolute product SLO remains unset and must be reported unverified;
the 600-second safety timeout is not a product SLO. Repeated synthetic inputs and
only 20 independent wave pairs in the concurrent cohort limit generalization.

Pilot 001 failed during browser setup before creating any upload: a frontend URL
without its trailing slash redirected HTTPS to HTTP behind the isolated proxy.
The harness URLs now include the slash. The failed pilot and diagnostics are
retained. Pilot 002 completed four measured uploads and one warm-up; pilot 003
completed ten longer-video uploads. Both passed byte integrity, semantic search,
decoded playback, durable-state/publication checks, and application cleanup.
These are functional calibration results, not evidence of primary p95 performance.

Primary collection has a nine-hour wall limit and stops on its first failed wave.
The dedicated host's absolute expiry is extended to **2026-11-11 09:00 UTC** before
collection, then stops campaign/browser/workers, captures evidence, and powers off.
The maximum host lifetime from creation is about 9.94 hours: approximately $4.01
compute, with an estimated $0.40 allowance for disk, IPv4, storage and requests,
within the $5 round ceiling. This is a planning estimate, not a billing guarantee.
The expected runtime from the pilots is about eight hours. No implicit extension,
replacement pooling, outcome-dependent sample reduction, or automatic rerun is
allowed. A stopped host retains its disk until verified preservation and teardown.
