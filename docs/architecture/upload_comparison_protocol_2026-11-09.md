# Controlled upload comparison protocol

Recorded before the measured campaign. Compare the retained API-proxied upload
path with the checksum-enabled direct S3 path in deployed commit
`a458bc2e5b19335025eed8ec63599e2c02297330`. This is an upload architecture comparison,
not a comparison of the processing DAG or of two production deployments.

## Isolation and controls

- Temporary EC2 host in the existing us-east-1 Vindex VPC, dedicated PostgreSQL
  database, private S3 bucket and least-privilege instance role. Production
  services and stored data are not benchmark targets.
- Both browser builds use the same source. One unchanged API container is limited
  to 0.5 vCPU and 2 GiB RAM, matching the deployed API's requested resources.
  PostgreSQL is separate from the measured container. No dispatcher or worker runs.
- TLS uses a test certificate, accepted only by the benchmark browser. Access is
  restricted to the runner's IP and a secret cookie; Cognito is replaced by the
  same local test identity in both modes. This excludes Cognito/CloudFront/API
  Gateway overhead. The production gateway cannot accept the large legacy bodies.
- Every attempt uses a fresh browser context/file selection. Timing starts at
  Upload click and ends at durable acceptance, including browser hashing, S3
  transfer and snapshot completion. AB/BA order alternates by paired repetition.
- Resource snapshots bracket the upload wave, excluding browser setup and later
  verification/cleanup. Collect process CPU seconds, /proc storage-write bytes,
  container network bytes, ASGI request/response body bytes, CPU throttling,
  and 50 ms sampled RSS/container memory. Storage-write accounting, cancelled
  writes, write-syscall bytes and logical API body volume are distinct measures;
  report them without equating them with physical device I/O or a billed volume.
  Memory includes caching effects.
- After every wave, retrieve and hash every accepted S3 input, confirm one video,
  one completed reservation and exactly two pending initial jobs per attempt,
  then delete that wave's test objects/rows. Preserve all failed-run evidence.
- Hard stop: the host automatically terminates after four hours. Resource IDs
  are journaled for explicit teardown. The test creates no recurring schedule.

## Corpus and primary cells

Synthetic H.264/AAC videos: 20 seconds at 1280x720/30fps and about 10.4 Mbps CBR,
then lossless concatenations to about 80 and 360 seconds. Files are actual media,
not arbitrary payload padding. Exact bytes/digests are recorded in fixtures.json.

| Cell | File bytes | Clients | Paired waves | Attempts per mode | Network cap per browser |
| --- | ---: | ---: | ---: | ---: | --- |
| Primary A | 27,164,499 | 1 | 100 | 100 | 20 Mbps upload, 40 ms added latency |
| Primary B | 27,164,499 | 5 | 20 | 100 | 20 Mbps upload, 40 ms added latency |
| Primary C | 108,652,341 | 5 | 20 | 100 | 20 Mbps upload, 40 ms added latency |

Pilot: two paired single-client waves of the small file, excluded from primary
statistics. Additional slower-network, ten-client or large-file cells are labeled
exploratory and are not pooled into the primary cells. Browser caps do not create
independent physical uplinks; record any aggregate runner network saturation.

## Decision and analysis rules

Retain every attempted upload, HTTP failure and timeout. Do not revise gates
after looking at results. Report each cell separately and use paired-wave
bootstrap confidence intervals (fixed seed, 10,000 resamples), preserving the
within-wave client grouping. Report medians and p95, absolute differences, and
direct/API ratios. A p95 with fewer than 100 accepted attempts is exploratory.

Existing proposed gates remain: API media/body traffic reduction at least 99%;
API process storage-write reduction at least 95%; direct p95 no more than
10% above API within a matched cell; observed acceptance at least 99% with its
uncertainty shown; no input corruption, duplicate logical videos or initial jobs.
For latency, an interval spanning the 1.10 ratio boundary is inconclusive. A
cell cannot establish a reliability population lower bound of 99% from just
100 successes. Report Wilson intervals descriptively and wave clustering as a
limitation; never claim independent reliability trials from concurrent clients.

CPU and memory changes are measured outcomes, without an invented pass threshold.
Reduced CPU/network work is not automatically a lower AWS bill: provisioned API
capacity can remain unchanged and direct upload adds S3 request/copy operations.
Report modeled request costs separately from the actual temporary test runtime.

Integrated search/playback under matched load is outside this upload-only run.
The completed deployment smoke demonstrates basic pipeline operation, not a
processing latency or throughput improvement. Conclusions must preserve this scope.
