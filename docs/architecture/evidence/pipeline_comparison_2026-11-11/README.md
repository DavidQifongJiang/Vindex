# Pipeline experiment evidence

The primary campaign contains **600 measured uploads**, six excluded warm-ups,
and 442 completed waves. See the [results and limits](../../pipeline_comparison_results_2026-11-11.md).
Pilot data remain separate from every primary estimate.

| Artifact | Purpose |
| --- | --- |
| `primary-raw.zip` | Byte-preserved primary JSON/JSONL: plan, progress, every attempt, milestones, input identity, runtime observations, verification, and per-wave cleanup |
| `primary-raw.zip.manifest.json` | Byte count and SHA256 for every ZIP entry |
| `measured-harness.zip` and its manifest | Exact source and locked plan retained on the experiment host before primary collection |
| `primary-analysis.json` | Paired-wave quantiles, confidence intervals, complete denominators, warm-up exclusion, and acceptance-gate outcomes |
| `primary-runtime.json` | Independent audit of runtime identities, resource limits, empty queues, cold restarts, warm continuity, locked-plan agreement, and clock origins |
| `warm-worker-log-audit.json` | Hashes and diagnostic counts from privately retained full warm-worker logs, including observed pool-worker identities |
| `comparison.png` / `comparison.svg` | Rendered primary search/playback p95 comparison; SVG retains editable text |
| `fixtures.json` | Exact speech-video bytes, SHA256, duration, codecs, and speech annotation |
| `pricing-inventory.json` | Read-only pre-experiment shared-service inventory and observed instance pricing |
| `cleanup-verification.json` | Final preservation, whole-bucket reconciliation, removal of owned resources, and shared-service health observations |
| `analysis-environment.json` | Analysis runtime versions and source hashes |
| `reproduction.json` | Verification of all 6,796 public raw entries and exact reproduction of analysis/audit results without cloud access |
| `pilot-raw.zip` and its manifest | Unchanged, byte-preserved evidence from pilots 001–003, including the failed setup pilot |
| `pilot-*-analysis.json` / `pilot-*-runtime.json` | Separate exploratory pilot summaries and runtime audits |
| `SHA256SUMS.json` | Checksums for every other file in this directory; it intentionally excludes itself |

Pilot 001 failed during browser setup before creating an upload and remains in
the evidence. Pilot 002 completed four measured uploads plus one excluded warm-up;
pilot 003 completed ten measured uploads. Their small samples do not establish
the primary p95 or population-reliability gates.

Raw console logs, database dumps, session state, and generated credentials remain
in the private local archive and are not published. Selected text was checked
against the generated private values and credential-shaped strings before export.
This selection does not change any published measurement values. The unfiltered
archives were downloaded and checked against their per-file SHA256 manifests.

Per-wave `cleanup.json.remaining_objects` counts the `raw_videos/` prefix, and
`remaining_vectors` counts all isolated Qdrant vectors. Application deletion
records its known artifact removals. The separate final receipt reconciles the
**entire bucket**; the per-wave field alone does not prove every prefix is empty.

## Reproduce without cloud access

Use Python 3.11 with NumPy 2.4.5. Plotting additionally uses Matplotlib 3.10.8.
The analysis and runtime audit require no application credentials or services.
Verify each ZIP entry against its adjacent manifest before extracting it.

From the repository root, choose a fresh local extraction directory:

```text
python -m zipfile -e docs/architecture/evidence/pipeline_comparison_2026-11-11/primary-raw.zip .review-cache/pipeline-reproduction
```

Then run from `backend/`:

```text
python -m benchmarks.analyze_pipeline_experiment --campaign ../.review-cache/pipeline-reproduction/primary-001 --output ../.review-cache/pipeline-reproduction/analysis.json
python -m benchmarks.audit_pipeline_experiment --campaign ../.review-cache/pipeline-reproduction/primary-001 --locked-plan benchmarks/pipeline_primary_2026-11-10.json --output ../.review-cache/pipeline-reproduction/runtime.json
python -m benchmarks.plot_pipeline_comparison --analysis ../.review-cache/pipeline-reproduction/analysis.json --output ../.review-cache/pipeline-reproduction/figures
```

Compare parsed analysis JSON after excluding only `generated_at`; every other
field must match `primary-analysis.json`. The runtime JSON must match in full.
The preregistered plan SHA256 is
`dd45f38db15cc3313a485743afc4ba315c38215f9cbf44efe60997891d9c83e4`.
The measured application commit is `a458bc2e5b19335025eed8ec63599e2c02297330`.

See the [execution record](../../pipeline_experiment_execution.md),
[measurement protocol](../../pipeline_readiness_protocol.md), and
[Vindex architecture diagrams](../../vindex_architecture_diagrams.md).
