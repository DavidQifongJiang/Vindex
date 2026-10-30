# Local monitoring validation — 2026-10-30

Validated against the existing local `vindex-api-1` container. Application code
and existing deployment configuration were not changed by this exercise.

- Docker Compose configuration validation: passed.
- Prometheus rule tests: passed (healthy, sustained failure/recovery, transient,
  missing telemetry).
- Alertmanager receiver configuration validation: passed.
- PowerShell, Python, YAML, and dashboard JSON parsing: passed.
- Grafana UI: loaded all eight panels and displayed real probe samples.
- Actual API pause/recovery drill: passed.
- Firing notification received: 2026-10-30 19:50:54 UTC.
- Resolved notification received: 2026-10-30 19:51:09 UTC.
- Final API probe: 1; API `/health`: ok; alert: inactive.
- Inbox UI visibly displayed both deliveries.

The drill began at 19:50:19 UTC and completed verification at 19:51:14 UTC.
These are one local exercise's results, not production reliability statistics.
Raw local evidence: `ops/learning/.state/drills.jsonl` (ignored by Git).

Scope: basic API liveness and local notification delivery. Search functionality,
database readiness, real on-call paging, and independently hosted monitoring
were not validated. The running image does not expose the newer `/search` route.
No active background benchmark was found during setup.
