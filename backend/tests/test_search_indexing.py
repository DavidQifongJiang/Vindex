from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.models import Base, Video, SearchProjection, SearchRevision, SearchAttempt, SearchDeletion, SearchConfiguration
from app.db import search_repository as repo
from app.services.search_indexing import process_projection, reconcile, specification, load_source
from app.services.processing_errors import StaleProcessingAttempt, TemporaryProcessingError
from app.services.projection_indexes import aligned_embeddings, ElasticsearchProjectionIndex, QdrantProjectionIndex


class Storage:
    def __init__(self):
        self.objects = {}

    def read_bytes(self, key):
        return self.objects[key]

    def read_json(self, key):
        return json.loads(self.read_bytes(key))


class MemoryIndex:
    def __init__(self):
        self.data = {}
        self.error = None
        self.after_write = None

    def write(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        checkpoint()
        self.data[attempt] = snapshot
        if self.error:
            raise self.error
        if self.after_write:
            self.after_write()

    def verify(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        checkpoint()
        return self.data.get(attempt) == snapshot

    def delete(self, attempt):
        self.data.pop(attempt, None)

    def close(self):
        pass


class SearchIndexingTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.setup_database()
        self.addCleanup(self.engine.dispose)

    def setup_database(self):
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.storage, self.index = Storage(), MemoryIndex()
        self.video_id = str(uuid4())
        self.path = "accepted/" + self.video_id
        self.env = patch.dict(os.environ, {"SEARCH_PROJECTIONS_ENABLED": "true", "SEARCH_EMBEDDING_REVISION": "",
            "SEARCH_LEXICAL_TARGET": "vindex-test-projections"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.storage.objects[self.path] = json.dumps([{"segment_id": "0:0", "start": 1, "end": 2, "text": "rareidentifier"}]).encode()
        with self.sessions() as db:
            db.add(Video(video_id=self.video_id, status="processed", segments_status="completed", segments_path=self.path))
            db.commit()
            repo.register_revision(db, self.video_id, self.path, self.storage.read_bytes(self.path))
            db.commit()
            self.job_id = db.query(SearchProjection.job_id).scalar()

    def run_job(self):
        return process_projection(self.job_id, self.storage, sessions=self.sessions, indexes=lambda *_: self.index)

    def test_registration_is_idempotent_and_mutation_is_rejected(self):
        with self.sessions() as db:
            first = db.get(Video, self.video_id).current_search_revision_id
            repo.register_revision(db, self.video_id, self.path, self.storage.read_bytes(self.path))
            db.commit()
            self.assertEqual(db.query(SearchRevision).count(), 1)
            self.assertEqual(db.query(SearchProjection).count(), 1)
            self.assertEqual(db.get(Video, self.video_id).current_search_revision_id, first)
            with self.assertRaises(ValueError):
                repo.register_revision(db, self.video_id, self.path, b'[]')

    def test_success_duplicate_delivery_and_ack_loss(self):
        result = self.run_job()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.run_job()["status"], "skipped")
        with self.sessions() as db:
            row = db.get(SearchProjection, self.job_id)
            self.assertEqual(row.accepted_attempt_id, result["projection_id"])
            self.assertEqual(db.query(SearchAttempt).count(), 1)
            self.assertEqual(db.get(Video, self.video_id).status, "processed")

    def test_partial_write_is_invisible_and_retry_uses_new_attempt(self):
        self.index.error = TemporaryProcessingError("network", error_code="network")
        self.run_job()
        old = next(iter(self.index.data))
        with self.sessions() as db:
            row = db.get(SearchProjection, self.job_id)
            self.assertEqual(row.status, "retrying")
            self.assertIsNone(row.accepted_attempt_id)
            row.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.index.error = None
        result = self.run_job()
        self.assertNotEqual(old, result["projection_id"])
        self.assertEqual(result["status"], "ready")

    def test_dead_worker_recovery_fences_old_publication(self):
        start = datetime.utcnow()
        with self.sessions() as db:
            old = repo.claim(db, self.job_id, now=start)
            repo.dispatch(db, lambda *_args, **_kwargs: None, now=start + timedelta(seconds=repo.LEASE_SECONDS + 1))
            row = db.get(SearchProjection, self.job_id)
            row.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
            replacement = repo.claim(db, self.job_id)
            self.assertNotEqual(old, replacement)
            with self.assertRaises(StaleProcessingAttempt):
                repo.publish(db, self.job_id, old)
            repo.publish(db, self.job_id, replacement)
            self.assertEqual(db.get(SearchProjection, self.job_id).accepted_attempt_id, replacement)

    def test_delete_during_write_prevents_publication_and_cleans_late_output(self):
        def delete():
            with self.sessions() as db:
                video = db.get(Video, self.video_id)
                video.status = "deleting"
                repo.revoke_video(db, self.video_id)
                db.delete(video)
                db.commit()
        self.index.after_write = delete
        self.assertEqual(self.run_job()["status"], "stale")
        self.assertEqual(len(self.index.data), 1)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(SearchDeletion, self.video_id))
            result = reconcile(db, self.storage, indexes=lambda *_: self.index, now=datetime.utcnow() + timedelta(days=1))
            self.assertEqual(result["cleaned_attempts"], 1)
            self.assertEqual(self.index.data, {})
            # A delayed external write after the first cleanup is collected again.
            attempt = db.query(SearchAttempt).one().attempt_id
            self.index.data[attempt] = "late"
            reconcile(db, self.storage, indexes=lambda *_: self.index, now=datetime.utcnow() + timedelta(days=2))
            self.assertEqual(self.index.data, {})

    def test_missing_index_schedules_repair_and_preserves_current_attempt_during_gc(self):
        result = self.run_job()
        with self.sessions() as db:
            checked = reconcile(db, self.storage, indexes=lambda *_: self.index, now=datetime.utcnow() + timedelta(days=1))
            self.assertEqual(checked["cleaned_attempts"], 0)
            self.assertIn(result["projection_id"], self.index.data)
            self.index.data.clear()
            repaired = reconcile(db, self.storage, indexes=lambda *_: self.index)
            self.assertEqual(repaired["repaired"], 1)
            self.assertIsNone(db.get(SearchProjection, self.job_id).accepted_attempt_id)
        self.assertEqual(self.run_job()["status"], "ready")

    def test_new_revision_invalidates_old_projection(self):
        self.run_job()
        new_path = self.path + "-revision2"
        with self.sessions() as db:
            db.get(Video, self.video_id).segments_path = new_path
            db.flush()
            repo.register_revision(db, self.video_id, new_path, b'[]')
            db.commit()
            self.assertEqual(db.get(SearchProjection, self.job_id).status, "obsolete")
            self.assertEqual(db.query(SearchProjection).count(), 2)

    def test_permanent_failure_and_exhaustion_require_explicit_repair(self):
        self.index.error = ValueError("invalid content")
        self.run_job()
        with self.sessions() as db:
            self.assertEqual(db.get(SearchProjection, self.job_id).status, "failed")
            self.assertEqual(db.get(Video, self.video_id).status, "processed")
            self.assertTrue(repo.request_repair(db, self.job_id))
            token = repo.claim(db, self.job_id)
            row = db.get(SearchProjection, self.job_id)
            row.max_attempts = 1
            db.commit()
            repo.fail(db, self.job_id, token, "unavailable", retryable=True)
            self.assertEqual(db.get(SearchProjection, self.job_id).status, "failed")

    def test_broker_failure_keeps_dispatch_intent(self):
        def lost(*_args, **_kwargs):
            raise RuntimeError("broker down")
        with self.sessions() as db:
            now = datetime.utcnow()
            with self.assertRaises(RuntimeError):
                repo.dispatch(db, lost, now=now)
            sent = []
            repo.dispatch(db, lambda *args, **kwargs: sent.append((args, kwargs)), now=now + timedelta(seconds=61))
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][1]["queue"], "indexing")

    def test_acceptance_and_intent_rollback_together(self):
        with self.sessions() as db:
            path = self.path + "-uncommitted"
            db.get(Video, self.video_id).segments_path = path
            db.flush()
            repo.register_revision(db, self.video_id, path, b'[]')
            db.rollback()
            self.assertEqual(db.query(SearchRevision).count(), 1)
            self.assertEqual(db.query(SearchProjection).count(), 1)
            self.assertEqual(db.get(Video, self.video_id).segments_path, self.path)


class EmbeddingAlignmentTest(unittest.TestCase):
    def test_reorders_by_identity_and_rejects_unknown_models_and_ambiguous_legacy_data(self):
        segments = [{"segment_id": "0:0", "start": 0, "end": 1, "text": "a"},
                    {"segment_id": "0:1", "start": 1, "end": 2, "text": "b"}]
        settings = {"model": "test", "model_revision": "pinned", "dimension": 2}
        embeddings = [dict(item, embedding=[i + 1, 1], embedding_model="test", embedding_model_revision="pinned")
                      for i, item in enumerate(segments)]
        self.assertEqual(aligned_embeddings(segments, embeddings[::-1], settings), [[1, 1], [2, 1]])
        for changed in [dict(embeddings[0], embedding_model_revision=None), dict(embeddings[0], text="wrong"),
                        dict(embeddings[0], embedding=[float("nan"), 1])]:
            with self.assertRaises(ValueError):
                aligned_embeddings(segments, [changed, embeddings[1]], settings)
        with self.assertRaises(ValueError):
            aligned_embeddings([segments[0], segments[0]], [embeddings[0], embeddings[0]], settings)


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Requires isolated Postgres")
class PostgresProjectionTest(SearchIndexingTest):
    def setUp(self):
        self.schema = "vindex_search_" + uuid4().hex
        self.root = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"])
        with self.root.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_database)
        self.setup_database()

    def cleanup_database(self):
        self.engine.dispose()
        with self.root.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))
        self.root.dispose()

    def test_two_claimers_get_only_one_owner(self):
        barrier = Barrier(2)
        def contender():
            with self.sessions() as db:
                barrier.wait(timeout=10)
                return repo.claim(db, self.job_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: contender(), range(2)))
        self.assertEqual(sum(token is not None for token in results), 1)

    def test_process_crash_after_claim_is_recovered(self):
        environment = dict(os.environ, VINDEX_TEST_SCHEMA=self.schema, VINDEX_TEST_JOB=self.job_id)
        child = subprocess.run([sys.executable, "-m", "tests.smoke.search_projection_crash"], env=environment,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 23, child.stderr)
        with self.sessions() as db:
            job = db.get(SearchProjection, self.job_id)
            old = job.token
            repo.dispatch(db, lambda *_a, **_kw: None, now=job.deadline_at + timedelta(seconds=1))
            db.get(SearchProjection, self.job_id).next_retry_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.run_job()["status"], "ready")
        with self.sessions() as db:
            with self.assertRaises(StaleProcessingAttempt):
                repo.publish(db, self.job_id, old)


@unittest.skipUnless(os.getenv("VINDEX_TEST_ELASTICSEARCH_URL"), "Requires Elasticsearch test URL")
class RealProjectionBackendTest(unittest.TestCase):
    def test_elasticsearch_isolation_idempotency_corruption_and_cleanup(self):
        from app.services.search_contracts import TranscriptSnapshot
        snapshot = TranscriptSnapshot.from_manifest(str(uuid4()), str(uuid4()), b'[{"start":0,"end":1,"text":"rareidentifier"}]')
        target = "vindex-phase2-test-" + uuid4().hex
        client = ElasticsearchProjectionIndex(target, os.environ["VINDEX_TEST_ELASTICSEARCH_URL"])
        a, b = str(uuid4()), str(uuid4())
        try:
            client.write(snapshot, a)
            client.write(snapshot, b)
            self.assertTrue(client.verify(snapshot, a))
            client.write(snapshot, a)
            self.assertTrue(client.verify(snapshot, a))
            self.assertEqual(len(client.search(b, "rareidentifier")), 1)
            client.delete(a)
            self.assertFalse(client.verify(snapshot, a))
            self.assertTrue(client.verify(snapshot, b))
            client.delete(a)
        finally:
            client.request("DELETE", target)
            client.close()


class LocalQdrantProjectionTest(unittest.TestCase):
    def test_actual_qdrant_normalization_payload_integrity_and_attempt_isolation(self):
        from qdrant_client import QdrantClient
        from app.services.search_contracts import TranscriptSnapshot
        client = QdrantClient(":memory:")
        self.addCleanup(client.close)
        index = QdrantProjectionIndex("test_projection", 2, client)
        snapshot = TranscriptSnapshot.from_manifest(str(uuid4()), str(uuid4()), b'[{"start":0,"end":1,"text":"hello"}]')
        a, b = str(uuid4()), str(uuid4())
        vectors = [[3., 4.]]
        index.write(snapshot, a, vectors)
        index.write(snapshot, b, vectors)
        self.assertTrue(index.verify(snapshot, a, vectors))
        index.delete(a)
        self.assertFalse(index.verify(snapshot, a, vectors))
        self.assertTrue(index.verify(snapshot, b, vectors))
