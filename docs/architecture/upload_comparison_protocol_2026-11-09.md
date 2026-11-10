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
| Primary A | 27,164,499 | 1 | 100 | 100 | 20 Mbps upload, CDP latency setting 40 ms |
| Primary B | 27,164,499 | 5 | 20 | 100 | 20 Mbps upload, CDP latency setting 40 ms |
| Primary C | 108,652,341 | 5 | 20 | 100 | 20 Mbps upload, CDP latency setting 40 ms |

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

## Predeclared deployment-layout confirmation

Added November 10 before any proxy-terminated measurements, after the first
five-client/small-file cell completed. The initial server handles TLS in Uvicorn,
whereas production terminates HTTPS upstream. This can change API CPU and the
point at which its 0.5-vCPU limit affects latency.

After the entire initial campaign completes, repeat all three cells with the
same files, browser settings, source, database, API CPU/memory limits and gates.
A separate Caddy container terminates HTTPS and streams requests to the API over
the private Docker network. Its CPU is separately counted using its read-only
cgroup statistics; the API remains limited to 0.5 vCPU and 2 GiB. Record the
proxy image digest and wrapper revision. Never combine the two layouts into one
distribution or choose the more favorable result. Production-oriented claims
use the upstream-TLS confirmation; the first campaign is retained as sensitivity
evidence. Neither layout reproduces the managed gateway's large-body rejection.

I/O interpretation follows the [Linux kernel accounting documentation](https://docs.kernel.org/filesystems/proc.html#proc-pid-io-display-the-io-accounting-fields):
write_bytes is counted when pages become dirty, and cancelled_write_bytes records
writes avoided by truncation/deletion. Report both, rather than calling their
unadjusted difference a measured device-level or billing improvement.

## Runner recovery amendment, November 10

The local Windows runner disappeared during the initial campaign. It recorded
200 completed small/five-client attempts and 50 accepted/verified medium attempts;
five additional in-flight direct-upload intents were found in residual metadata.
The campaign has no complete marker and cannot support a full-run acceptance
claim. All recorded rows and the five metadata objects are preserved. The old
dedicated resources were removed after preservation; no production data was used.

Before collecting any replacement data, move the generator to a dedicated
`c7i.xlarge` (4 vCPU, 8 GiB) with pinned Playwright Chromium and Node 22.23.4.
The API uses a fresh isolated host/database/bucket and upstream Caddy TLS as above.
Repeat all three predeclared cells (600 attempts) with the same corpus, network
settings, API limits, sample sizes, analysis and gates. The remote generator writes
30-second S3 checkpoints and a final hash manifest; both temporary hosts expire
after four hours. Source, runtime and browser versions are captured.

The replacement run is a separate dataset. Do not pool it with the interrupted
Windows run or attribute cross-run differences solely to HTTPS termination: the
client hardware/browser also changed. Primary conclusions compare upload modes
within the completed remote dataset. A cloud client with emulated network limits
does not reproduce arbitrary consumer networks or client hardware.

### Failure-evidence operations

S3 checkpoints preserve report files, and unfinished S3 inputs remain in the
dedicated bucket until explicitly preserved/removed or its two-day lifecycle
expires. PostgreSQL state resides on the temporary host. If a wave fails, export
that database and download residual inputs before the host's expiry deadline;
the automatic four-hour shutdown is a spending bound, not a database backup.
Explicit teardown refuses residual inputs and re-verifies the local report
archive before removing resources. Keep an active foreground watch until a
completed run is downloaded and verified, then remove the isolated resources.
