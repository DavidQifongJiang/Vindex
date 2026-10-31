# Learn monitoring on your local Vindex

This first exercise uses Vindex's running API, a real Blackbox Exporter,
Prometheus, Grafana, Alertmanager, and a local webhook inbox. It develops
operational judgment: observe a baseline, detect impact, investigate, recover,
and record evidence. Allow 20–30 minutes for your first run.

## Current baseline

The source checkout already contains operational metrics and 14 alert rules in
`ops/alerts.yml`. The existing Prometheus configuration only scrapes the
maintenance exporter; library-search scraping is an opt-in example.
The default Alertmanager receiver sends nothing.

At setup on 2026-10-30, the running local image responded successfully to
`/health`, but did not expose `/search` in OpenAPI. It predates the newer search
implementation in the checkout. This exercise does not rebuild that image,
run migrations, restart workers, or enable background maintenance. The root
Compose override includes AWS mounts, so do not casually run the entire stack
just to learn monitoring.

The new monitoring-only Compose file is additive. It uses existing local
network `vindex_default` to probe `vindex-api-1:8000/health`. It does not load
the operational alert set because its exporter is not running in this baseline.
Those rules remain available for a later exercise.

## Start and open

Run in PowerShell from the Vindex repository, with Docker Desktop's Linux engine
and the existing local Vindex API running:

```powershell
.\ops\learning\Learn-Monitoring.ps1 -Action Start
.\ops\learning\Learn-Monitoring.ps1 -Action Status
```

| Tool | Local address | Responsibility |
| --- | --- | --- |
| Learning page and inbox | http://127.0.0.1:18780/ | Instructions and received firing/resolved notifications |
| Grafana | http://127.0.0.1:13000/d/vindex-learning/vindex-monitoring-practice | Visualize probe data |
| Prometheus | http://127.0.0.1:19090/alerts | Store samples and evaluate rules |
| Alertmanager | http://127.0.0.1:19093/ | Group, route and deliver notifications |
| Blackbox Exporter | http://127.0.0.1:19115/ | Make HTTP probes of Vindex |
| Vindex | http://127.0.0.1:8000/health | Actual application under observation |

All new published ports bind to loopback. Grafana provides anonymous viewing
for this local exercise. Do not publish these services publicly. Monitoring
containers do not auto-start after a reboot; run Start again when practicing.
Stop removes only this learning project's containers, retaining its volumes:

```powershell
.\ops\learning\Learn-Monitoring.ps1 -Action Stop
```

## Exercise 1 — the API stops answering

1. Open Grafana and the inbox side by side. Wait until both API probe and probe
   collection are 1. The inbox should have no new failure notifications.
2. Predict the sequence before touching the system: failed probe, pending
   rule, firing rule, delivered notification, successful probe, resolved delivery.
3. When nobody is using the local app, run:

   ```powershell
   .\ops\learning\Learn-Monitoring.ps1 -Action Drill
   ```

   This pauses only the local API container for 45 seconds. Requests to the
   local API will fail/time out during the pause. The script checks the Compose
   identity and loopback binding, then resumes the same container in a finally
   block. It does not stop the database, change records, or delete data.
   If the PowerShell host is forcibly killed, restore manually:

   ```powershell
   docker unpause vindex-api-1
   ```

4. Watch the graph and Prometheus Alerts page. A rule must remain true for 20
   seconds. Evaluation happens every 5 seconds; Alertmanager groups for another
   5 seconds, so delivery is not instantaneous. Probe timeout and scheduling add
   delay. These deliberately short timings are learning settings, not SLOs.
5. Investigate with `docker ps`, the Blackbox probe debug result, and the API
   logs. A pause does not necessarily produce an application error log:
   absence of logs is not evidence of health.

   ```powershell
   docker ps --filter name=vindex-api-1
   docker logs --since 5m --tail 30 vindex-api-1
   Invoke-WebRequest 'http://127.0.0.1:19115/probe?target=http%3A%2F%2Fvindex-api-1%3A8000%2Fhealth&module=vindex_health&debug=true'
   ```

6. Confirm API probe returns to 1, /health responds, the rule becomes inactive,
   and a resolved notification arrives. The script asserts these outcomes.
   Local evidence is appended to ignored `ops/learning/.state/drills.jsonl`.
7. Complete [the incident worksheet](INCIDENT.md). Explain the causal chain
   before adding another monitoring tool.

## Read the signals correctly

`probe_success` means the health request passed its HTTP 200 and response-body
checks. `up` means Prometheus successfully collected the exporter response.
During an API outage, **probe_success can be 0 while up remains 1**. The observer
is healthy and is reporting that its target is unhealthy.

The 5-minute percentage is the fraction of successful sampled health probes,
not a ratio of real user requests. Probe duration includes DNS, connection,
and request time; it is not the search API's p95.

The separate missing-probe rule handles a broken exporter or absent probe
series. An alert-free dashboard with no data must not be read as healthy.
The local inbox is a teaching receiver, not an on-call paging service. It
stores the latest 100 deliveries in memory and appends local JSONL history
inside its Docker volume. It does not contact Slack, email, or a phone.

## What comes next

1. **Monitoring failure:** stop only the learning Blackbox service; verify the
   missing-probe alert, restart it, and verify recovery. Distinguish observer
   failure from application failure.
2. **User-facing search:** prepare a separately reviewed local deployment of
   the current code, enable authenticated metrics scraping, and observe actual
   search outcomes/duration. A health endpoint alone cannot prove search works.
3. **Define an SLO:** choose a user journey and valid request population, set a
   target with a time window, then build an actionable alert and runbook.
   Base thresholds on traffic and the product's needs, not copied sample numbers.
4. **Production delivery:** configure a real on-call receiver, owner, schedule,
   escalation, notification test, and an independently hosted uptime check.
5. **Trace investigation:** add OpenTelemetry when you need to follow failures
   across components. The existing Prometheus path lets you practice monitoring
   now without first migrating instrumentation.

## Configuration checks

The project reuses the checkout's cached Prometheus 3.2.1 and Alertmanager 0.28.1,
and pins Blackbox Exporter 0.29.1 and Grafana 13.3.0. These are lab versions, not a
production upgrade recommendation. Rule tests cover healthy traffic, sustained
failure and recovery, brief failures, and missing telemetry.

```powershell
docker compose -p vindex-monitoring-learning -f docker-compose.monitoring.yml config --quiet
$rules = (Resolve-Path ops/learning).Path
docker run --rm --network none --mount "type=bind,source=$rules,target=/work,readonly" --workdir /work --entrypoint /bin/promtool prom/prometheus:v3.2.1 test rules alerts.test.yml
```

Authoritative background:
[HTTP probes](https://prometheus.io/docs/guides/multi-target-exporter/),
[alert evaluation](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[notification routing](https://prometheus.io/docs/alerting/latest/configuration/),
[rule tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/).

