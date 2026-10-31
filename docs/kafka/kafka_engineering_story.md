# Vindex: making search events recoverable and replayable

Interview preparation, September 18, 2026. Based on
[validation results](kafka_validation_results.md), not production traffic.
Use the scripts below only where you can explain and demonstrate the implementation.

## Resume bullet

Built Kafka-backed search analytics with a PostgreSQL transactional outbox and two
independent consumer groups; verified processing and replay of 180,000 synthetic
events locally and tested idempotent crash and outage recovery.

## The 90-second story

“Vindex supports semantic search through Qdrant and keyword search through
Elasticsearch. I wanted to understand failed searches and let users share examples
for review. Those are two consumers of the same interaction history, with different
processing and replay needs.

I kept Celery for video-processing jobs and added Kafka for retained search events.
The API first writes an optional search observation to a PostgreSQL outbox. A
publisher forwards accepted events to Kafka, and separate consumer groups build
analytics and review projections. That keeps broker availability out of retrieval.

The difficult part was defining failure boundaries. A publisher can crash after
Kafka accepts a record, and a consumer can crash after updating PostgreSQL but
before committing its Kafka offset. I handled redelivery with stable event IDs and
transactional receipts, and tested both crash windows. Replay builds a separate
projection over saved offset ranges so I can compare results without replacing
the live dashboard.

The early implementation missed its freshness target. I changed the consumer to
bounded batches and reduced database lock duration. A later 30-minute local run
accounted for all 180,000 accepted events in both groups, with about 324ms p95
projection freshness. I also tested broker and consumer outages.

The remaining weakness is capture latency: pooled search overhead meets the
target, but individual rounds do not, and immediate feedback availability is low.
I describe this as a validated local prototype, with explicit acceptance gaps.”

## Five-minute walkthrough

| Time | Explain | Evidence to open |
| --- | --- | --- |
| 0:00–0:45 | Product problem: aggregate search outcomes and inspect consented failure examples. Kafka is an engineering exploration of independent consumers and replay; current traffic does not require it. | Plan: outcome and rationale |
| 0:45–1:30 | Search → optional Postgres context/outbox → publisher → Kafka → two groups → separate Postgres projections. Celery remains responsible for processing jobs. | Worker and repository files |
| 1:30–2:30 | Walk through ack-before-mark and DB-before-offset crashes. Show where duplicate delivery becomes one database effect. | Crash integration tests and unique receipts |
| 2:30–3:30 | Explain the failed early freshness run, bounded batches, budget-lock changes and measured sustained result. Do not attribute the entire improvement to one change. | Original failed report, sustained report and reconciliation |
| 3:30–4:15 | Show frozen replay ranges and equal checksums. Explain that authorization can make review content unavailable later. | Replay manifest and access tests |
| 4:15–5:00 | Explain the latency gate that remains open, capture losses, low feedback capability rate, and the next experiment. | Latest per-round overhead audit |

## Decisions worth defending

| Decision | Reason | Cost or boundary |
| --- | --- | --- |
| PostgreSQL outbox | Avoid an uncoordinated database/broker dual write; durable acceptance precedes publication. | Adds database writes, cleanup, a budget counter and publisher operation. |
| Two consumer groups | Analytics and review can progress and replay independently. | More processes and independently monitored lag. |
| At-least-once delivery with receipts | PostgreSQL effects and Kafka offsets cannot be assumed to commit together. | Duplicate transport work is expected; receipts must live with the projection generation. |
| Up to 50 records / 100ms collection | Amortize transaction and offset-commit overhead while bounding work. | More buffering and rebalance handling; batching has a freshness tradeoff. |
| Key by search ID | Keep related events on one partition. | Publisher retries/concurrency can still change business arrival order; joins tolerate it. |
| Shadow replay with saved bounds | Compare a deterministic retained input range without moving live offsets. | Retention can remove required history; live dashboard replacement is deferred. |
| Private query context in Postgres | Keep raw queries and passage content out of Kafka; recheck consent and access when reviewing. | Review history can become unavailable even while opaque events remain retained. |
| Bounded optional capture | Preserve retrieval when analytics persistence is slow or unavailable. | Some observations are lost; late commits may not enable feedback in the response. |

## Hiring-manager questions

**Why Kafka instead of another Celery queue?**

“Celery already handles commands to process videos. Here I wanted retained events
that two applications can consume independently and revisit after a handler change.
Kafka provides consumer offsets and retained-log replay directly. A Celery-based
solution would need additional event storage and replay coordination.”

**Was Kafka necessary at your scale?**

“No scaling requirement has established that. A PostgreSQL event table with two
consumer cursors is a credible simpler option. I chose Kafka to implement and test
independent consumption and replay, and I would compare the operational cost with
that simpler option before a production rollout.”

**What does retry-safe mean here?**

“After durable outbox acceptance, retries may deliver the same event again. Each
projection transaction records a unique receipt and applies its effects together.
A repeat delivery finds the receipt and does not apply the effect twice. This is
idempotent database processing, not an end-to-end exactly-once guarantee.”

**What happens when Kafka goes down?**

“Search does not call Kafka. Accepted observations wait in the bounded outbox.
In a 60-second local outage drill, both groups eventually accounted for all 12,000
accepted events, and the pre-restart backlog recovered in 21.48 seconds. A separate
real-search drill completed 1,212 requests during broker downtime without HTTP
errors or changed rankings. Capture remains best effort before acceptance.”

**What was the hardest failure?**

“An early 6,000-attempt run had about 38.66 seconds p95 freshness. I treated
admission rate and completed processing rate separately, implemented bounded
consumer batches and shortened budget-lock ownership, then ran the sustained
fixture. The later result was about 324ms p95. Multiple implementation changes
separate those runs, so I would not call that an isolated batching speedup.”

**Did every search produce an event?**

“No. The final search experiment persisted 4,210 of 4,232 attempted observations,
including warmups and the outage phase. Only 772 of 3,000 measured enabled requests
returned immediate feedback capability. The capture deadline can expire before a
successful commit, so missing capability is not the same as a lost event.”

**Did it pass the latency requirement?**

“Not consistently. Across 6,000 requests, pooled p95 increased by 8.05ms for
semantic search and 12.97ms for keyword search. Some individual rounds exceeded
the 20ms or 10% limits, including a combined-mode round. I retained those failures
and left the gate open. The next step is to profile scheduling, transaction and
commit time before changing the capture interaction.”

**How do you know replay is correct?**

“I save retained start and exclusive-end offsets, rebuild a fresh generation,
and compare counts and checksums. The 180,000-event replay matched both live
projections. That establishes deterministic agreement for the fixture, not that
both implementations cannot share a bug; separate tests cover expected semantics,
duplicates, malformed input, consent and access boundaries.”

**What would production require next?**

“First, resolve the latency and feedback-availability gaps and run all gates on
the final code version. Then validate the supported CI runtimes and an authenticated
live demo. Production work would include broker authentication and encryption,
replication and failure-domain tests, storage and retention planning, operational
alerts and recovery procedures. The current one-broker setup establishes restart
recovery only.”

## Numbers and qualifications to remember

| Number | Exact meaning |
| --- | --- |
| 180,000 / 30 minutes | Synthetic completion events at 100/s; pre-final capture SQL optimization; both groups accounted for every accepted event. |
| ~324ms p95 | Event-to-post-database-commit projection freshness, not search API latency. |
| 99.515s | Shadow replay duration; its final comparison initially timed out, then separate paginated reconciliation confirmed equality. |
| 4.281s / 21.484s | Recovery of the pre-restart backlog after separate 60s consumer/broker outages while new traffic continued. |
| 6,000 requests | 500 queries × two modes × on/off × three rounds; no changed paired rankings, not a new relevance-quality score. |
| 22 / 4,232 | Observations missing before durable acceptance, not downstream loss of accepted outbox events. |
| 11 integration tests | Current capture code checked with real Postgres/Kafka, including crash, rebalance and capacity behavior. |

## Code and evidence map

- [Capture boundary](../app/services/search_events.py): two bounded capture slots,
  25ms await, late-commit distinction.
- [Transactions](../app/db/search_event_repository.py): atomic context/outbox,
  budget reservation, receipts and projection batches.
- [Workers](../app/workers/search_events.py): publication, consumer offsets,
  rebalance behavior, replay and maintenance.
- [Integration tests](../../backend/tests/test_search_events_integration.py) and
  [rebalance test](../../backend/tests/test_search_events_rebalance.py).
- [Full results and caveats](kafka_validation_results.md) and
  [reproduction runbook](kafka_search_analytics_runbook.md).

For interview preparation, rehearse a crash sequence and locate its transaction
boundary in the code. Explain which claims come from tests, benchmarks or design
intent. Keep the failed reports available; do not present hypothetical future
improvements as delivered work.
