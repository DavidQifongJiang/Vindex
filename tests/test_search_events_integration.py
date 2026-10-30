"""Run with VINDEX_TEST_KAFKA_BOOTSTRAP and VINDEX_TEST_DATABASE_URL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text, func
from app.db.models import Base
from app.db.search_event_models import (SearchEventBudget as Budget, SearchEventOutbox as Outbox,
    SearchEventReceipt as Receipt, SearchEventProjection as Projection, SearchEventCheckpoint as Checkpoint,
    SearchEventContext as Context)
from app.db import search_event_repository as repo
from app.services.search_events import completion
from app.workers import search_events as worker


@unittest.skipUnless(os.getenv("VINDEX_TEST_KAFKA_BOOTSTRAP") and os.getenv("VINDEX_TEST_DATABASE_URL"), "isolated Kafka/Postgres required")
class SearchEventIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "event_test_"+uuid4().hex[:12]
        self.topic = "vindex.test."+self.schema
        self.engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"])
        with self.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.env = patch.dict(os.environ, {"SEARCH_EVENTS_DATABASE_URL": os.environ["VINDEX_TEST_DATABASE_URL"],
            "SEARCH_EVENTS_KAFKA_BOOTSTRAP": os.environ["VINDEX_TEST_KAFKA_BOOTSTRAP"],
            "SEARCH_EVENTS_SCHEMA": self.schema, "SEARCH_EVENTS_TOPIC": self.topic,
            "SEARCH_EVENTS_SYNTHETIC_ONLY": "true"})
        self.env.start()
        repo.sessions.cache_clear()
        self.factory = repo.sessions()
        Base.metadata.create_all(self.factory.kw["bind"])
        with self.factory.begin() as db:
            db.add(Budget(name="outbox", pending_bytes=0, expired_events=0))
        worker.bootstrap()

    def tearDown(self):
        from confluent_kafka.admin import AdminClient
        admin = AdminClient(worker.connection())
        admin.delete_topics([self.topic])[self.topic].result(15)
        self.factory.kw["bind"].dispose()
        repo.sessions.cache_clear()
        self.env.stop()
        with self.engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def capture(self):
        e = completion(str(uuid4()), "semantic", 200,
            {"results": [], "effective_mode": "semantic", "coverage": {"complete": True, "total_videos": 1}}, 10)
        repo.capture_search("owner", "public example", e, [], True)
        return e

    def test_concurrent_duplicate_effects(self):
        e = self.capture()
        def apply(_):
            with self.factory.begin() as db:
                return repo.apply_event(db, "analytics-v1", e)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(apply, range(20))), 1)
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Receipt)), 1)

    def test_capture_reservation_failure_rolls_back_both_inserts(self):
        with patch.dict(os.environ, {"SEARCH_EVENTS_OUTBOX_BYTES": "1"}):
            with self.assertRaises(ValueError):
                self.capture()
        with self.factory.begin() as db:
            self.assertEqual(db.query(Context).count(), 0)
            self.assertEqual(db.query(Outbox).count(), 0)
            self.assertEqual(db.get(Budget, "outbox").pending_bytes, 0)
            db.delete(db.get(Budget, "outbox"))
        with self.assertRaises(ValueError):
            self.capture()
        with self.factory() as db:
            self.assertEqual(db.query(Context).count(), 0)
            self.assertEqual(db.query(Outbox).count(), 0)

    def test_concurrent_capture_never_exceeds_capacity_and_duplicate_rolls_back(self):
        from sqlalchemy.exc import IntegrityError
        events = [completion(str(uuid4()), "semantic", 200, {"results": []}, 10) for _ in range(2)]
        limit = max(len(repo.encoded(e)) for e in events)
        def capture(e):
            try:
                repo.capture_search("owner", "q", e, [], False)
                return e
            except ValueError:
                return None
        with patch.dict(os.environ, {"SEARCH_EVENTS_OUTBOX_BYTES": str(limit)}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                accepted = [e for e in pool.map(capture, events) if e]
        self.assertEqual(len(accepted), 1)
        with self.assertRaises(IntegrityError):
            repo.capture_search("owner", "q", accepted[0], [], False)
        with self.factory() as db:
            self.assertEqual(db.query(Context).count(), 1)
            self.assertEqual(db.query(Outbox).count(), 1)
            self.assertEqual(db.get(Budget, "outbox").pending_bytes, len(repo.encoded(accepted[0])))

    def test_publisher_crash_ack_and_republication(self):
        e = self.capture()
        result = subprocess.run([sys.executable, "-m", "tests.search_events_crash", "publisher"], timeout=30,
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 71, result.stderr.decode(errors="replace"))
        with self.factory.begin() as db:
            row = db.get(Outbox, e["event_id"])
            self.assertIsNone(row.published_at)
            old_token = row.token
            row.lease_until = repo.utcnow()-timedelta(seconds=1)
        worker.publish_once(worker.producer())
        repo.mark_published(e["event_id"], old_token)
        with tempfile.TemporaryDirectory() as directory:
            result = worker.replay(Path(directory)/"replay.json")
            self.assertEqual(result["events"], 2)
            self.assertEqual(result["searches"], 1)
        with self.factory() as db:
            self.assertEqual(db.get(Budget, "outbox").pending_bytes, 0)

    def test_consumer_crash_before_offset_commit(self):
        self.capture()
        worker.publish_once(worker.producer())
        crashed = subprocess.run([sys.executable, "-m", "tests.search_events_crash", "consumer"], timeout=40,
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(crashed.returncode, 72, crashed.stderr.decode(errors="replace"))
        with self.factory() as db:
            checkpoint = db.execute(select(Checkpoint).where(Checkpoint.next_offset > 0)).scalar_one()
            # Group commit was never created: require explicit recovery instead of silently resetting.
            with self.assertRaises(ValueError):
                repo.starting_offset("analytics-v1", self.topic, checkpoint.partition, -1001, 0, 1)
        # Restart the original production group and prove it advances without
        # duplicating the receipt already committed by the crashed process.
        from confluent_kafka import TopicPartition
        stop = threading.Event()
        errors = []
        def consume():
            try:
                worker.run_consumer("analytics-v1", stop)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=consume)
        thread.start()
        c = worker.consumer("search-analytics-v1")
        try:
            deadline = time.monotonic()+30
            committed = -1
            while time.monotonic() < deadline:
                committed = c.committed([TopicPartition(self.topic, checkpoint.partition)], timeout=5)[0].offset
                if committed == 1 or errors:
                    break
                time.sleep(.1)
            self.assertEqual(errors, [])
            self.assertEqual(committed, 1)
            with self.factory() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(Receipt)), 1)
        finally:
            stop.set()
            thread.join(10)
            c.close()

    def test_postgres_dashboard_batch_and_saved_replay_bounds(self):
        e = self.capture()
        repo.consume_batch("analytics-v1", [(self.topic, 0, 0, repo.encoded(e))])
        self.assertEqual(next(iter(repo.dashboard()["modes"].values()))["searches"], 1)
        worker.publish_once(worker.producer())
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory)/"one.json"
            first = worker.replay(original)
            self.capture()
            worker.publish_once(worker.producer())
            second = worker.replay(Path(directory)/"two.json", original)
        self.assertEqual(first["projection_sha256"], second["projection_sha256"])
        self.assertEqual(second["events"], 1)

    def test_retention_gap_rejected(self):
        self.assertEqual(repo.starting_offset("analytics-v1", self.topic, 0, -1001, 0, 10), 0)
        with self.factory.begin() as db:
            db.get(Checkpoint, ("analytics-v1", self.topic, 0)).next_offset = 5
        for committed, low in ((-1001, 0), (2, 3), (6, 0)):
            with self.assertRaises(ValueError):
                repo.starting_offset("analytics-v1", self.topic, 0, committed, low, 10)

    def test_corrected_handler_replays_unchanged_quarantined_bytes(self):
        self.capture()
        worker.publish_once(worker.producer())
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory)/"bad-handler.json"
            with patch.object(repo.SearchEvent, "model_validate_json", side_effect=ValueError("simulated handler bug")):
                bad = worker.replay(original)
            repaired = worker.replay(Path(directory)/"corrected.json", original)
        self.assertEqual((bad["quarantined"], bad["searches"]), (1, 0))
        self.assertEqual((repaired["quarantined"], repaired["searches"]), (0, 1))
        self.assertEqual(bad["offsets"], repaired["offsets"])

    def test_migration_upgrade_downgrade_upgrade(self):
        migration_schema = "migration_"+uuid4().hex[:12]
        with self.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{migration_schema}"'))
        try:
            env = dict(os.environ, DATABASE_URL=os.environ["VINDEX_TEST_DATABASE_URL"],
                PGOPTIONS=f"-c search_path={migration_schema}")
            for command in (["upgrade", "head"], ["downgrade", "20260916_0006"], ["upgrade", "head"]):
                result = subprocess.run([sys.executable, "-m", "alembic", *command], env=env,
                    capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            with self.engine.connect() as db:
                self.assertEqual(db.scalar(text(f'SELECT pending_bytes FROM "{migration_schema}".search_event_budget')), 0)
                self.assertEqual(db.scalar(text(f'SELECT count(*) FROM "{migration_schema}".search_event_consents')), 0)
        finally:
            with self.engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{migration_schema}" CASCADE'))

    def test_two_groups_replay_and_quarantine(self):
        e = self.capture()
        worker.publish_once(worker.producer())
        p = worker.producer()
        p.produce(self.topic, key=e["search_id"], value=None)
        p.flush(10)
        with tempfile.TemporaryDirectory() as directory:
            first = worker.replay(Path(directory)/"one.json")
            second = worker.replay(Path(directory)/"two.json")
        self.assertEqual(first["projection_sha256"], second["projection_sha256"])
        self.assertEqual(first["quarantined"], 1)
        repo.retire_shadow(first["generation"])
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Receipt).where(Receipt.generation == first["generation"])), 0)


if __name__ == "__main__":
    unittest.main()
