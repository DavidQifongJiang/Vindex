"""Subprocess fault injection at real transport/database commit boundaries."""
import os
import sys
import time
from app.db import search_event_repository as repo
from app.workers import search_events as worker


if sys.argv[1] == "publisher":
    producer = worker.producer()
    batch = repo.claim_batch(1)
    if not batch:
        raise RuntimeError("no event")
    identity, search_id, event, token = batch[0]
    acknowledged = []
    producer.produce(worker.topic(), key=search_id.encode(), value=repo.encoded(event),
        on_delivery=lambda error, message: acknowledged.append(error is None))
    producer.flush(15)
    if acknowledged != [True]:
        raise RuntimeError("not acknowledged")
    os._exit(71)  # Kafka accepted; outbox publication was not recorded.
else:
    client = worker.consumer("search-analytics-v1")
    def assigned(c, partitions):
        committed = c.committed(partitions, timeout=10)
        for p in committed:
            low, high = c.get_watermark_offsets(p, timeout=10)
            p.offset = repo.starting_offset("analytics-v1", p.topic, p.partition, p.offset, low, high)
        c.assign(committed)
        c.commit(offsets=committed, asynchronous=False)
    client.subscribe([worker.topic()], on_assign=assigned)
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        msg = client.poll(.5)
        if msg and not msg.error():
            repo.consume_record("analytics-v1", msg.topic(), msg.partition(), msg.offset(), msg.value())
            os._exit(72)  # Database accepted; group offset was not committed.
    raise RuntimeError("no message")
