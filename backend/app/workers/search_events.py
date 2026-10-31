"""Explicit optional worker CLI: bootstrap, publish, consume, maintain, replay."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from app.db import search_event_repository as repo


def connection():
    return {"bootstrap.servers": os.getenv("SEARCH_EVENTS_KAFKA_BOOTSTRAP", "127.0.0.1:19092")}


def topic():
    return os.getenv("SEARCH_EVENTS_TOPIC", "vindex.search.interactions.v1")


def producer():
    from confluent_kafka import Producer
    return Producer(dict(connection(), **{"enable.idempotence": True, "acks": "all",
        "delivery.timeout.ms": 10000, "request.timeout.ms": 5000, "linger.ms": 5,
        "queue.buffering.max.messages": 1000, "queue.buffering.max.kbytes": 16384}))


def bootstrap():
    from confluent_kafka.admin import AdminClient, NewTopic
    admin = AdminClient(connection())
    if topic() not in admin.list_topics(timeout=10).topics:
        admin.create_topics([NewTopic(topic(), 3, 1, config={"retention.ms": "604800000",
            "retention.bytes": "1073741824", "segment.ms": "3600000", "min.insync.replicas": "1"})])[topic()].result(20)


def publish_once(client):
    delivered = []
    batch = repo.claim_batch()
    for event_id, search_id, event, token in batch:
        def ack(error, message, identity=event_id, claim=token):
            if error is None:
                delivered.append((identity, claim))
        client.produce(topic(), key=search_id.encode(), value=repo.encoded(event), on_delivery=ack)
    client.flush(11)
    repo.mark_published_batch(delivered)
    return len(delivered)


def consumer(group):
    from confluent_kafka import Consumer
    return Consumer(dict(connection(), **{"group.id": group, "enable.auto.commit": False,
        "enable.auto.offset.store": False, "auto.offset.reset": "error", "session.timeout.ms": 10000,
        "max.poll.interval.ms": 60000, "allow.auto.create.topics": False}))


def run_consumer(generation, stop=None):
    from confluent_kafka import TopicPartition
    client = consumer("search-"+generation)
    pending = []
    def revoked(c, partitions):
        # No offsets have been stored for this buffer. Its new owner will replay it.
        pending.clear()
    def assigned(c, partitions):
        pending.clear()
        committed = c.committed(partitions, timeout=10)
        # New groups begin at retained earliest; existing expired offsets must error.
        for p in committed:
            low, high = c.get_watermark_offsets(p, timeout=10)
            p.offset = repo.starting_offset(generation, p.topic, p.partition, p.offset, low, high)
        c.assign(committed)
        # Persist initial positions before processing; a first-record crash can
        # then resume from a known offset instead of looking like lost history.
        c.commit(offsets=committed, asynchronous=False)
    def flush():
        if not pending:
            return
        records = [(m.topic(), m.partition(), m.offset(), m.value()) for m in pending]
        started = time.monotonic()
        repo.consume_batch(generation, records)
        if os.getenv("SEARCH_EVENTS_MEASURE_FRESHNESS") == "true":
            committed_at = datetime.now(timezone.utc)
            delays = []
            for _, _, _, raw in records:
                try:
                    delays.append((committed_at-datetime.fromisoformat(json.loads(raw)["occurred_at"])).total_seconds())
                except (ValueError, TypeError, KeyError):
                    pass
            print(json.dumps({"event": "batch_committed", "db_ms": (time.monotonic()-started)*1000,
                "freshness_seconds": delays}), flush=True)
        offsets = {}
        for name, partition, offset, _ in records:
            offsets[name, partition] = offset+1
        # A failed commit exits and replays the committed DB transaction safely.
        client.commit(offsets=[TopicPartition(name, p, offset)
            for (name, p), offset in sorted(offsets.items())], asynchronous=False)
        pending.clear()
    client.subscribe([topic()], on_assign=assigned, on_revoke=revoked, on_lost=revoked)
    batch_started = time.monotonic()
    try:
        while stop is None or not stop.is_set():
            if pending and (len(pending) >= 50 or time.monotonic()-batch_started >= .1):
                flush()
            msg = client.poll(max(0, .1-(time.monotonic()-batch_started)) if pending else .1)
            if msg is None:
                continue
            if msg.error():
                raise RuntimeError(str(msg.error()))
            if not pending:
                batch_started = time.monotonic()
            pending.append(msg)
        flush()
    finally:
        client.close()


def replay(output, source_manifest=None):
    """Offline shadow replay; never replaces the live dashboard."""
    from confluent_kafka import TopicPartition
    from sqlalchemy import select
    from app.db.search_event_models import SearchEventProjection, SearchEventQuarantine
    generation = "replay-"+str(uuid4())
    client = consumer(generation)
    bounds, positions, count = {}, {}, 0
    try:
        metadata = client.list_topics(topic(), timeout=10)
        saved = json.loads(Path(source_manifest).read_text(encoding="utf-8")) if source_manifest else None
        if saved and saved["topic"] != topic():
            raise ValueError("replay_topic_mismatch")
        partitions = sorted(metadata.topics[topic()].partitions)
        if saved and set(map(int, saved["offsets"])) != set(partitions):
            raise ValueError("replay_partition_set_changed")
        starts = []
        for p in partitions:
            low, high = client.get_watermark_offsets(TopicPartition(topic(), p), timeout=10)
            if saved:
                start, end = saved["offsets"][str(p)]
                if not low <= start <= end <= high:
                    raise ValueError("replay_range_no_longer_available")
                low, high = start, end
            bounds[p] = [low, high]
            positions[p] = low
            starts.append(TopicPartition(topic(), p, low))
        client.assign(starts)
        deadline = time.monotonic()+600
        while any(positions[p] < bounds[p][1] for p in partitions):
            if time.monotonic() > deadline:
                raise TimeoutError("replay incomplete")
            records = []
            for msg in client.consume(num_messages=50, timeout=.1):
                if msg.error():
                    raise RuntimeError(str(msg.error()))
                p = msg.partition()
                if msg.offset() >= bounds[p][1]:
                    continue
                records.append((topic(), p, msg.offset(), msg.value()))
            if records:
                repo.consume_batch(generation, records)
                for _, p, offset, _ in records:
                    positions[p] = offset+1
                count += len(records)
        with repo.sessions()() as db:
            rows = db.execute(select(SearchEventProjection).where(SearchEventProjection.generation == generation)
                .order_by(SearchEventProjection.search_id)).scalars().all()
            states = [{"search_id": r.search_id, "state": r.state} for r in rows]
            quarantined = len(db.execute(select(SearchEventQuarantine).where(SearchEventQuarantine.generation == generation)).scalars().all())
        manifest = dict(generation=generation, topic=topic(), offsets=bounds, events=count,
            projection_sha256=sha256(repo.encoded(states)).hexdigest(), searches=len(states), quarantined=quarantined,
            repair_policy="unchanged Kafka bytes with corrected handler; payload replacements are new events",
            scope="retained offsets only; offline shadow, no live replacement")
        manifest["schema_version"] = 1
        manifest["handler_sha256"] = sha256(Path(repo.__file__).read_bytes()).hexdigest()
        Path(output).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["bootstrap", "publish", "consume", "maintain", "replay", "retire"])
    parser.add_argument("--generation", default="analytics-v1")
    parser.add_argument("--output", default="tmp/search-events-replay.json")
    parser.add_argument("--manifest")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "replay":
        print(json.dumps(replay(args.output, args.manifest)))
    elif args.command == "retire":
        repo.retire_shadow(args.generation)
    elif args.command == "consume":
        if args.generation not in {"analytics-v1", "review-v1"}:
            parser.error("live consumers require analytics-v1 or review-v1")
        while True:
            try:
                run_consumer(args.generation)
            except ValueError:
                # Lost history requires explicit operator recovery, never a reset.
                raise
            except Exception as exc:
                print(json.dumps({"event": "consumer_retry", "error_type": type(exc).__name__}), flush=True)
                time.sleep(1)
    elif args.command == "maintain":
        while True:
            repo.maintenance()
            if args.once:
                break
            time.sleep(60)
    else:
        client = producer()
        while True:
            try:
                publish_once(client)
            except Exception as exc:
                print(json.dumps({"event": "publisher_retry", "error_type": type(exc).__name__}), flush=True)
            time.sleep(.25)


if __name__ == "__main__":
    main()
