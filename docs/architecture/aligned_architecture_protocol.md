# Aligned architecture comparison

Status: all 24 primary trials and nine calibration trials complete. See the
[results and evidence](aligned_architecture_results_2026-11-12.md).
The primary plan was registered before collection.
Current registered plan: [24 fresh trials](evidence/aligned_architecture_2026-11-12/primary-002-plan.json),
SHA-256 `7835a8f58317c02b50917d4849246dac2d6c3e781ecbf5daa380fa14bbc7b642`.
The [first registration](evidence/aligned_architecture_2026-11-12/primary-plan.json)
stopped at its first trial because exact floating-point timestamp comparisons
rejected Qdrant serialization roundoff (maximum observed difference 5.68e-14 s).
All seven retained inputs matched segment counts and text. The failed attempt
was preserved and all 24 trials rerun; that attempt is not pooled with the replacement.
The repaired identity check retains exact text/cardinality and permits at most
one nanosecond of timestamp roundoff. Workloads and performance gates are unchanged.

## Question and reusable evidence

Compare A (legacy whole-video processing + API upload), B (independent playback
and whole-video search branches + API upload), C (five-minute chunked DAG + API
upload), and D (same chunked DAG + direct S3 upload). Record both newly uploaded video
readiness and existing-video search responsiveness in every trial. Fixed API,
worker pool, host, models, input bytes, client shaping and search traffic.

The previous 600-upload readiness comparison and 24-trial mixed-load comparison
remain valid for their registered workloads. Reuse their pinned application image,
browser builds, short speech fixture, protected search corpus procedure, scheduled
arrival generators, isolation, integrity checks and evidence/cleanup procedures.
Do not pool their observations with this campaign: worker capacity, workload mix
and readiness observation change. Earlier 24%/82% monolith comparisons are context,
not matched observations in the new experiment.

## Baseline reconstruction

Use the retained `process_video_task` / `process_video` implementation in pinned
application commit a458bc2e5b19335025eed8ec63599e2c02297330. It executes transcode,
thumbnail, whole-video audio extraction, transcription, embedding and publication
serially. An isolated benchmark adapter creates legacy Video-backed pending work
instead of DAG jobs and republishes unclaimed legacy work every five seconds.
Existing claim/token and retry checks remain intact. This is a reconstruction
using the retained legacy implementation, not deployment of a historical release.

B uses the existing durable DAG with a single whole-video search work unit
(`SEARCH_CHUNK_SECONDS=1800`, longer than the 900-second fixture). C/D change only
that setting to 300 seconds. This preserves the same lease/publication code when
isolating chunking. A uses the retained historical monolithic implementation;
A -> B therefore includes its existing extraction/publication differences as part
of the branch redesign, not a claim that scheduling is the only changed code.

B/C/D use the existing durable dispatcher. All modes share one
four-process Celery pool, four vCPU and 10 GiB, consuming all three queues. A can
process different videos concurrently; only the stages of each video are serial.
Thus the baseline is not artificially restricted to one worker. All worker child
processes warm the same Whisper tiny and MiniLM models before measurement.
Primary warmup records the current child PID/start-time set and samples that set
throughout observation, invalidating a trial if a child is replaced. Container
image references, commands, quotas and start identities are recorded as well.
Between trials, stop the dispatcher before draining workers; only processing
workers need the full shutdown allowance. This does not change the timed window.

The adapter is guarded against non-experiment databases, buckets and queues.
No application source, shared service or production flag changes.

## Workload and measures

Each trial uploads one 15-minute synthetic speech video to exercise three
five-minute chunks (five-second overlap), alongside the previous 25.8 MB / 20s
speech fixture at a low and high offered arrival rate. Existing-video semantic
search continues at two requests/second against three protected processed videos.
Browser uploads retain 20 Mbps / 40 ms shaping; search uses the same independent
client network in all modes.

Primary choices: background upload rates 3 and 60/minute, 30s baseline,
120s load, 120s recovery, three repetitions per configuration/rate. Calibration
may adjust durations/rates before the exact primary plan is registered and hashed.
Run A -> B -> C -> D within each repetition as requested; alternate low/high rate
order between repetitions. Warm every worker before each trial and record each
trial's no-upload search baseline. The fixed stage order remains a limitation.
Preserve all calibration and primary failures. The exact primary plan has 24
trials: four configurations, two traffic levels, three repetitions.

Report A -> B, B -> C, C -> D and all other observed pairwise comparisons. For a
lower-is-better metric, reduction = 100 * (before - after) / before. Retain raw
units, per-repetition values and paired ratios. Do not add percentage reductions;
compute the cumulative A -> D change directly. An observed pairwise comparison
does not establish an untested intermediate architecture or causal interaction.

Long-video readiness starts at the browser's successful upload acknowledgement,
ending at the first semantic response with an expected spoken phrase and valid
timestamp. Report click-to-ready separately. Poll at two-second intervals; retain
last-negative/first-positive bounds. Censored/failed attempts stay in denominators.
Only confirmed missing-hit responses establish a negative bound, using the probe
start time. Transport errors and other HTTP failures remain inconclusive probes.
Only three independent long videos per cell: report every observation, median and
paired differences, not a population p95 or SLA.

Search responsiveness is the per-trial p95 of scheduled existing-video queries,
reported separately from readiness seconds. Also report API/worker CPU, upload
latency, accepted and completed videos, backlog count/age, failures and correctness.
No sustainable-capacity claim when backlog grows.

## Gates and scope

Reuse search p95 <=1s and <=1.20 times matched baseline, >=99% query correctness
and upload acceptance, complete unique arrival ledgers, generator p99 lag <=100ms,
no pool exhaustion/restart/OOM, and <90% sampled client quota/memory utilization.
Every accepted input must match its fixture bytes/hash and publication lineage.
Legacy publications require matching artifact/vector cardinality; DAG publications
also require completed chunk tokens. Long video must contain nonempty spoken
content across its duration; preserve transcript/chunk coverage checks.
Recorded client results are immutable across restarts; replay their evidence
publication without rerunning the workload or replacing a failure with a new attempt.

No improvement threshold is required to publish a result. No selective removal of
failed trials, changes to gates after primary collection, or claims about actual
customer traffic, billing savings, production availability or broad search quality.
Started trials that abort before cleanup remain explicit incomplete observations;
unreturned commits and missing evidence prevent a comparative improvement claim.

## Resources and completion

Dedicated m7i.2xlarge server and m7i.xlarge client, current combined EC2 price
$0.6048/hour (AWS pricing inventory 2026-11-11). The original eight-vCPU client
was refused by the account quota; the replacement has four vCPU and 16 GiB.
The initial three-vCPU upload generator exceeded its 90% CPU gate in the
high-rate direct trial. Preserve that invalid calibration. Assign 3.5 vCPU to
uploads and 0.25 vCPU to queries, and check the full primary load duration in a
separate calibration before registration. Generator headroom gates remain
unchanged. This full-duration calibration passed: upload-generator peak CPU
82.7% of its allocation, 121 verified uploads, and 540 correct scheduled queries.
The primary plan retains this client allocation for every stage. Estimated compute ceiling $5,
maximum five-hour lifetime including setup and cleanup; storage/IP/requests extra.
Automatic graceful expiry and independent hard stops. Keep shared ECS capacity
unchanged. Preserve/download/hash-verify evidence before guarded deletion.
Publish harness, exact primary plan, all outcomes, reproducible analysis and
cleanup verification through a separate PR. No Kafka benchmark is running.
