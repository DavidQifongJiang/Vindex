# Pipeline experiment evidence

This directory currently contains completed pilot evidence. Primary results are
pending and must not be inferred from these small samples.

- `pilot-raw.zip`: byte-preserved JSON/JSONL measurements, runtime conditions,
  verification, and cleanup records from pilots 001–003. Its manifest records
  the size and SHA256 of each entry. Pilot 001 failed during setup before upload
  and is retained; pilots 002 and 003 completed. Pilot 002's warm-up is excluded
  from its four measured attempts. Pilot 003 has ten measured attempts.
- `pilot-*-analysis.json`: exploratory paired-wave summaries. Samples are too
  small to establish the primary p95 or reliability gates.
- `pilot-*-runtime.json`: audits of actual container identities, CPU/memory
  limits, empty queues, process restarts, and common measurement time origins.

Raw console logs, database dumps, session state, and generated credentials remain
in the private local archive and are not published. Selected JSON was checked
against the generated private values and credential-shaped strings before export.
This selection does not change any published measurement values. The unfiltered
archives were downloaded and checked against their per-file SHA256 manifests.

Per-wave `cleanup.json.remaining_objects` counts the `raw_videos/` prefix, and
`remaining_vectors` counts all isolated Qdrant vectors. Application deletion
records its known artifact removals. Full-bucket reconciliation is required at
final teardown; the per-wave field alone does not prove every S3 prefix is empty.

See [execution record](../../pipeline_experiment_execution.md) and
[measurement protocol](../../pipeline_readiness_protocol.md).
