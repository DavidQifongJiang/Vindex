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
- Current stage: seed creation and exploratory calibration. No capacity claim yet.

The first calibration has two trials, API then direct: 12 upload arrivals/minute, 2 semantic queries/second, 16 prepared upload contexts, 60 seconds baseline + 90 seconds load + 30 seconds recovery, 180-second upload deadline, 25,808,821-byte speech fixture. New-video processing is paused after verifying the three protected seed videos.

The server runs `mixed_campaign.py`; the client independently runs `mixed_client.py`. Their control channel is the owned bucket. Each trial is archived before teardown; progress appears under `evidence/<campaign>/progress.json`. Local desktop disconnection does not stop a dispatched campaign. The archive is downloaded and hash-verified before resource deletion. No background Kafka benchmark is part of this experiment.

Remaining gates: validate client timing/headroom and corpus correctness; freeze primary rates/repetitions; complete paired comparison; repeat selected rates with workers active; analyze product outcomes and limits; preserve final evidence; remove both hosts and associated resources; finish PR checks.
