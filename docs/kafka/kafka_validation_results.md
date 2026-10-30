# Kafka local validation results

September 18, 2026. **Validated local prototype; not all acceptance gates passed.**
This report supersedes earlier status notes in the implementation handoff and
resume review. Optional features remain disabled by default. No cloud rollout
or production high-availability validation was performed.

## Evidence

| Validation | Observed result | Status |
| --- | --- | --- |
| Sustained stream | 180,000 synthetic events at 100/s for 30 minutes; 180,000 receipts in each group; post-commit freshness p95 0.324s in both groups | Passed on the pre-CTE capture version; consumer batching unchanged |
| Replay | 180,000 projections rebuilt in 99.515s; both live generations and replay have identical checksums | Passed by separate paginated reconciliation; original final comparison timed out and is retained |
| Consumer outage | 60s stop; other group continued; pre-restart backlog recovered in 4.281s; all 12,000 events accounted in both groups | Passed |
| Broker outage | 60s stop; pre-restart backlog recovered in 21.484s; all 12,000 accepted events accounted | Passed |
| Real searches during broker outage | 1,212 searches in 60.032s; no HTTP errors or changed rankings | Passed locally, four ASGI clients and real search stores |
| Crash, replay and boundary integration | 11 real Postgres/Kafka tests including rebalance, crash windows, migration and atomic capture capacity | Passed on current capture code |
| Current capture stream screen | 1,000 accepted events, both groups fully accounted, replay equal, post-commit p95 <=0.426s | Passed; not a replacement for a current-version 30-minute run |
| Backend discovery | 300 tests: 234 passed, 66 skipped | Passed available tests; skipped tests are not validation |
| Frontend | TypeScript/Vite build and browser fixture passed, including retry IDs and consent withdrawal | Mock API fixture, not a full authenticated live-stack demo |
| Search overhead | Pooled results pass; individual rounds fail the original limits | Open |
| Deployment / CI runtime | Local Python 3.12 and Node 24; configured CI Python 3.11 and Node 22 | CI execution not verified |

## Search overhead and capture

`tmp/kafka-overhead-final-02` contains 6,000 measured requests: 500 queries,
two modes, capture on/off, three paired rounds, four clients. Conditions are
randomly interleaved; this measures incremental capture under mixed traffic,
not an entire deployment with analytics fully off versus fully on.

| Mode | Capture off p95 | Capture on p95 | Increase | Pooled target |
| --- | --- | --- | --- | --- |
| Semantic / Qdrant | 316.84ms | 324.89ms | 8.05ms / 2.54% | Pass |
| Keyword / Elasticsearch | 169.73ms | 182.70ms | 12.97ms / 7.64% | Pass |

All measured requests returned HTTP 200; no paired rankings changed. These are
latency and ranking-preservation results, not a relevance-quality experiment.

Pooling hides variation: round 1 keyword increased 10.93%; round 2 semantic
increased 26.07ms, and keyword increased 34.48ms / 20.31%. Even combining both modes,
round 1 increased 23.07ms. Therefore the three-run <=20ms and <=10% gate is
**not consistently met**. The benchmark now reports each round explicitly.
The earlier failed run `tmp/kafka-overhead-final-01` is retained.

Including warmups and the separate outage phase, 4,210 of 4,232 observations
persisted (99.48%); 22 were lost before durable acceptance. Only 772 of 3,000
measured capture-enabled searches returned immediate interaction capability
(25.73%). Late commits can contribute analytics without enabling feedback on
the returned page. This is a material feedback-availability limitation. Optional
capture loss must not be confused with loss after durable outbox acceptance.

## Reproduction evidence and resource limits

- Sustained: `tmp/kafka-batch-30min-01/report.json` and `reconciliation.json`.
- Outages: `tmp/kafka-consumer-outage-01` and `tmp/kafka-broker-outage-01`.
- Latest search timing: `tmp/kafka-overhead-final-02/results.json`,
  `round_audit.json`, and `outage_requests.json`.
- Current-code integration: `tmp/kafka-cte-integration.log`.
- Current-code discovery: `tmp/kafka-final-current-suite.log`.
- Browser screenshots: `tmp/kafka-ui/desktop.png` and `mobile.png`.

The sustained run sampled Kafka CPU up to 205.23% and memory 658.5MiB;
Postgres CPU up to 29.27% and memory 829.6MiB. Schema disk usage before replay
was 783,360,000 bytes. Container block-I/O counters are cumulative, not isolated
run deltas; these are sampled container measurements, not total host resource costs.
Kafka used one local broker, three partitions, replication factor 1 and minimum
ISR 1. Restart recovery does not establish disk-loss or host/zone tolerance.

## Resume and remaining work

Supported bullet:

> Built Kafka-backed search analytics with a PostgreSQL transactional outbox and
> two independent consumer groups; verified processing and replay of 180,000
> synthetic events locally and tested idempotent crash and outage recovery.

The 180,000-event claim refers to the recorded implementation version, before
the final capture SQL optimization. Do not claim all gates passed, production
scale, exactly-once delivery, or lossless capture of every search.

Next engineering work is to profile capture scheduling, transaction and commit
time under fixed load, improve immediate feedback availability, and repeat the
predeclared three-round latency experiment without selecting favorable rounds.
Rerun the 30-minute stream on the final capture version before claiming complete
current-version acceptance. A live authenticated demo and CI execution remain
separate checks. Live dashboard replacement and reviewer-entered judgments stay
deferred as agreed.
