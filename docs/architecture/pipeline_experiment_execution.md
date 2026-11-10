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
