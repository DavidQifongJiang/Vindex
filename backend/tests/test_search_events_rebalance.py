"""Real same-group partition handoff; requires isolated Kafka and Postgres."""
import os
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.search_event_models import SearchEventReceipt, SearchEventProjection
from app.db import search_event_repository as repo
from app.services.search_events import completion
from app.workers import search_events as worker
from tests import test_search_events_integration as integration


@unittest.skipUnless(os.getenv("VINDEX_TEST_KAFKA_BOOTSTRAP") and os.getenv("VINDEX_TEST_DATABASE_URL"),
                     "isolated Kafka/Postgres required")
class SearchEventRebalanceTests(unittest.TestCase):
    # Reuse isolated fixture lifecycle without inheriting or discovering its tests.
    setUp = integration.SearchEventIntegrationTests.setUp
    tearDown = integration.SearchEventIntegrationTests.tearDown

    def test_real_members_join_leave_and_redeliver_without_duplicate_effects(self):
        original_consumer = worker.consumer
        lock = threading.Lock()
        ownership, callbacks, errors = {}, [], []
        stops = {name: threading.Event() for name in ("member-one", "member-two")}
        threads = []
        expected = set()
        producer = worker.producer()
        # Monitoring must not compete for the two production worker connections.
        observer_engine = create_engine(self.factory.kw["bind"].url,
            connect_args={"options": f"-c search_path={self.schema}"}, pool_size=1, max_overflow=0)
        observer_sessions = sessionmaker(observer_engine)

        class ObservedConsumer:
            def __init__(self, group):
                self.name = threading.current_thread().name
                self.client = original_consumer(group)

            def __getattr__(self, name):
                return getattr(self.client, name)

            def subscribe(self, topics, **handlers):
                def wrap(kind, handler):
                    def observed(client, partitions):
                        handler(client, partitions)
                        with lock:
                            callbacks.append((self.name, kind, tuple(p.partition for p in partitions)))
                            ownership[self.name] = ({p.partition for p in partitions}
                                                    if kind == "on_assign" else set())
                    return observed
                self.client.subscribe(topics, **{kind: wrap(kind, handler) for kind, handler in handlers.items()})

        def start(name):
            def run():
                try:
                    worker.run_consumer("analytics-v1", stops[name])
                except Exception as exc:
                    with lock:
                        errors.append((name, repr(exc)))
            thread = threading.Thread(target=run, name=name)
            threads.append(thread)
            thread.start()

        def wait_until(predicate, description):
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                with lock:
                    self.assertEqual(errors, [], f"consumer failed during {description}")
                if predicate():
                    return
                time.sleep(.05)
            self.fail(f"Timed out: {description}; callbacks={callbacks}")

        def assigned(name, partitions):
            with lock:
                return ownership.get(name) == partitions

        def split_assignment():
            with lock:
                first, second = ownership.get("member-one", set()), ownership.get("member-two", set())
                return bool(first and second) and not first.intersection(second) and first.union(second) == {0, 1, 2}

        def publish_stage():
            acknowledged = []
            for index in range(12):
                event = completion(str(uuid4()), "semantic", 200,
                    {"results": [], "effective_mode": "semantic", "coverage": {"complete": True, "total_videos": 1}}, 10)
                expected.add(event["event_id"])
                # Explicit placement exercises every partition; duplicated delivery
                # verifies effects, not merely a matching total after reassignment.
                for _ in range(2):
                    producer.produce(self.topic, partition=index % 3, key=event["search_id"],
                        value=repo.encoded(event), on_delivery=lambda error, message: acknowledged.append(error))
            self.assertEqual(producer.flush(10), 0)
            self.assertEqual(acknowledged, [None] * 24)

        def all_effects_present():
            with observer_sessions() as db:
                receipts = set(db.scalars(select(SearchEventReceipt.event_id).where(
                    SearchEventReceipt.generation == "analytics-v1")))
            return receipts == expected

        try:
            with patch.object(worker, "consumer", ObservedConsumer):
                start("member-one")
                wait_until(lambda: assigned("member-one", {0, 1, 2}), "first member owns all partitions")
                publish_stage()
                wait_until(all_effects_present, "first-stage effects")

                start("member-two")
                wait_until(split_assignment, "two real members split partition ownership")
                publish_stage()
                wait_until(all_effects_present, "effects while both members run")

                stops["member-one"].set()
                threads[0].join(15)
                self.assertFalse(threads[0].is_alive(), "first member did not close")
                wait_until(lambda: assigned("member-two", {0, 1, 2}), "remaining member acquires all partitions")
                publish_stage()
                wait_until(all_effects_present, "post-handoff effects")
                with observer_sessions() as db:
                    rows = db.scalars(select(SearchEventProjection).where(
                        SearchEventProjection.generation == "analytics-v1")).all()
                    self.assertEqual(len(rows), 36)
                    self.assertTrue(all(row.state["completed"] for row in rows))
                with lock:
                    self.assertTrue(any(name == "member-one" and kind == "on_revoke"
                                        for name, kind, _ in callbacks))
                    self.assertGreaterEqual(sum(name == "member-two" and kind == "on_assign"
                                                for name, kind, _ in callbacks), 2)
        finally:
            for stop in stops.values():
                stop.set()
            for thread in threads:
                thread.join(15)
            observer_engine.dispose()
            self.assertTrue(all(not thread.is_alive() for thread in threads), "consumer thread did not stop")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
