from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Barrier, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from botocore.exceptions import ClientError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.models import Base, PipelineJob, Video, VideoSearchChunk
from app.db.pipeline_repository import (
    claim_work, complete_work, fail_work, has_active_work, renew_work, work_query,
)
from app.db.video_repository import create_video, create_search_chunks, claim_video_for_processing
from app.services.processing_errors import PermanentProcessingError, StaleProcessingAttempt, TemporaryStorageError
from app.workers.pipeline_dispatcher import dispatch_pending, recover_expired, REDISPATCH_SECONDS
from app.workers.durable_task import run_durable_task


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.versions = {}
        self.lock = Lock()
        self.fail_final_write = False
        self.on_final_write = None

    def exists(self, key):
        return key in self.objects

    def write_text(self, key, value):
        self._write(key, value)

    def write_json(self, key, value, **kwargs):
        self._write(key, value)

    def _write(self, key, value):
        if "/final/" in key and self.fail_final_write:
            raise TemporaryStorageError("Final artifact write failed")
        self.objects[key] = value
        if key.startswith("embeddings/final/") and self.on_final_write:
            self.on_final_write()

    def read_text(self, key):
        return self.objects[key]

    def read_json(self, key):
        return self.objects[key]

    def read_json_versioned(self, key):
        with self.lock:
            return deepcopy(self.objects.get(key)), self.versions.get(key)

    def write_json_conditional(self, key, value, etag):
        with self.lock:
            if self.versions.get(key) != etag:
                raise ClientError({"Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
            self.objects[key] = deepcopy(value)
            self.versions[key] = str(uuid4())

    def scan_objects(self, prefix, cursor=None, limit=200):
        keys = sorted(key for key in self.objects if key.startswith(prefix) and key > (cursor or ""))[:limit]
        return [{"Key": key, "LastModified": datetime.now().astimezone() - timedelta(days=2)} for key in keys], keys[-1] if len(keys) == limit else None

    def save_upload(self, fileobj, key, max_bytes):
        content = fileobj.read()
        if len(content) > max_bytes:
            raise ValueError("too large")
        self.objects[key] = content
        return len(content)

    def download_file(self, key, path):
        path.write_bytes(self.objects[key])

    def upload_file(self, path, key):
        self.objects[key] = path.read_bytes()

    def delete_many(self, keys):
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)

    def delete_prefix(self, prefix):
        return self.delete_many([key for key in self.objects if key.startswith(prefix)])


class DurablePipelineTest(unittest.TestCase):
    database_url = "sqlite:///:memory:"

    def setUp(self):
        self.root_engine = create_engine(self.database_url)
        self.schema = None
        self.engine = self.root_engine
        if self.engine.dialect.name == "postgresql":
            self.schema = "vindex_test_" + uuid4().hex
            with self.root_engine.begin() as connection:
                connection.execute(CreateSchema(self.schema))
            self.engine = self.root_engine.execution_options(schema_translate_map={None: self.schema})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.addCleanup(self.cleanup)
        create_video(self.db, {
            "video_id": "v1", "owner_user_id": "u1", "filename": "v1.mp4",
            "status": "processing", "embedding_status": "processing",
        })

    def cleanup(self):
        self.db.close()
        if self.schema:
            with self.root_engine.begin() as connection:
                connection.execute(DropSchema(self.schema, cascade=True))
        self.root_engine.dispose()

    def chunks(self, count=1):
        return create_search_chunks(self.db, "v1", [{
            "chunk_index": index, "start_seconds": index * 300,
            "end_seconds": (index + 1) * 300, "audio_start_seconds": max(0, index * 300 - 5),
            "audio_end_seconds": (index + 1) * 300 + 5, "audio_path": f"audio/{index}.wav",
        } for index in range(count)])

    def due(self, model, key):
        work_query(self.db, model, "v1", key).update({"next_retry_at": datetime.utcnow() - timedelta(seconds=1)})
        self.db.commit()

    def expire(self, model, key):
        work_query(self.db, model, "v1", key).update({"lease_expires_at": datetime.utcnow() - timedelta(seconds=1)})
        self.db.commit()

    def test_upload_and_initial_outbox_are_one_transaction(self):
        create_video(self.db, {"video_id": "v2", "status": "uploaded"}, enqueue_processing=True)
        self.assertEqual({job.stage for job in self.db.query(PipelineJob).filter_by(video_id="v2")}, {"search", "playback"})
        self.assertIsNone(claim_video_for_processing(self.db, "v2", "legacy-worker", 3))
        with patch("app.db.video_repository.add_job", side_effect=RuntimeError("outbox insert failed")):
            with self.assertRaises(RuntimeError):
                create_video(self.db, {"video_id": "v3"}, enqueue_processing=True)
        self.db.rollback()
        self.assertIsNone(self.db.get(Video, "v3"))

    def test_broker_outage_and_lost_delivery_are_redispatched(self):
        self.chunks()
        now = datetime.utcnow()
        publish = Mock(side_effect=ConnectionError("Redis down"))
        with self.assertLogs("app.workers.pipeline_dispatcher", level="ERROR"):
            self.assertEqual(dispatch_pending(self.db, publish, now=now), 0)
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).processing_attempt_count, 0)
        publish.side_effect = None
        self.assertEqual(dispatch_pending(self.db, publish, now=now + timedelta(seconds=1)), 0)
        self.assertEqual(dispatch_pending(self.db, publish, now=now + timedelta(seconds=REDISPATCH_SECONDS + 1)), 1)
        self.assertEqual(dispatch_pending(self.db, publish, now=now + timedelta(seconds=2 * REDISPATCH_SECONDS + 2)), 1)
        claim_work(self.db, VideoSearchChunk, "v1", 0, "worker")
        self.assertEqual(dispatch_pending(self.db, publish, now=now + timedelta(hours=1)), 0)

    def test_duplicate_claim_and_expired_attempt_are_fenced(self):
        self.chunks()
        first = claim_work(self.db, VideoSearchChunk, "v1", 0, "old-worker").processing_token
        self.assertIsNone(claim_work(self.db, VideoSearchChunk, "v1", 0, "duplicate"))
        self.expire(VideoSearchChunk, 0)
        self.assertEqual(recover_expired(self.db), 1)
        self.due(VideoSearchChunk, 0)
        second = claim_work(self.db, VideoSearchChunk, "v1", 0, "new-worker").processing_token
        self.assertNotEqual(first, second)
        with self.assertRaises(StaleProcessingAttempt):
            complete_work(self.db, VideoSearchChunk, "v1", 0, first, {"transcript_path": "stale"})
        self.assertIsNone(fail_work(self.db, VideoSearchChunk, "v1", 0, first, TemporaryStorageError("late failure")))
        with self.assertRaises(StaleProcessingAttempt):
            renew_work(self.db, VideoSearchChunk, "v1", 0, first)
        current = self.db.get(VideoSearchChunk, ("v1", 0))
        self.assertEqual(current.processing_token, second)
        self.assertIsNone(current.transcript_path)

    def test_heartbeat_prevents_recovery_and_expired_worker_cannot_renew(self):
        self.chunks()
        token = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker").processing_token
        renew_work(self.db, VideoSearchChunk, "v1", 0, token)
        self.assertEqual(recover_expired(self.db), 0)
        self.expire(VideoSearchChunk, 0)
        with self.assertRaises(StaleProcessingAttempt):
            renew_work(self.db, VideoSearchChunk, "v1", 0, token)

    def test_database_attempt_limit_survives_new_message_ids(self):
        self.chunks()
        for attempt in range(1, 4):
            row = claim_work(self.db, VideoSearchChunk, "v1", 0, f"delivery-{uuid4()}")
            self.assertEqual(row.processing_attempt_count, attempt)
            row = fail_work(self.db, VideoSearchChunk, "v1", 0, row.processing_token, TemporaryStorageError("S3 unavailable"))
            self.assertEqual(row.status, "failed" if attempt == 3 else "retrying")
            if attempt < 3:
                self.due(VideoSearchChunk, 0)
        self.assertIsNone(claim_work(self.db, VideoSearchChunk, "v1", 0, "fresh-message"))
        self.assertEqual(self.db.get(Video, "v1").embedding_status, "failed")

    def test_repair_terminalizes_exhausted_retrying_rows(self):
        self.chunks()
        row = self.db.get(VideoSearchChunk, ("v1", 0))
        row.status, row.processing_attempt_count = "retrying", 3
        self.db.commit()
        self.assertEqual(recover_expired(self.db), 1)
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "failed")

    def test_chunk_completion_atomically_schedules_one_finalizer(self):
        self.chunks(2)
        for index in range(2):
            token = claim_work(self.db, VideoSearchChunk, "v1", index, "worker").processing_token
            complete_work(self.db, VideoSearchChunk, "v1", index, token)
            self.assertEqual(self.db.query(PipelineJob).filter_by(stage="finalize").count(), index)
        self.assertIsNone(claim_work(self.db, VideoSearchChunk, "v1", 1, "duplicate"))
        self.assertEqual(self.db.query(PipelineJob).filter_by(stage="finalize").count(), 1)

    def test_finalizer_failure_does_not_change_completed_chunks(self):
        self.chunks()
        token = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker").processing_token
        complete_work(self.db, VideoSearchChunk, "v1", 0, token)
        first = claim_work(self.db, PipelineJob, "v1", "finalize", "worker").processing_token
        fail_work(self.db, PipelineJob, "v1", "finalize", first, TemporaryStorageError("merge upload failed"))
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "completed")
        self.due(PipelineJob, "finalize")
        second = claim_work(self.db, PipelineJob, "v1", "finalize", "replacement").processing_token
        with self.assertRaises(StaleProcessingAttempt):
            complete_work(self.db, PipelineJob, "v1", "finalize", first)
        complete_work(self.db, PipelineJob, "v1", "finalize", second)
        self.assertEqual(self.db.get(PipelineJob, ("v1", "finalize")).processing_attempt_count, 2)

    def test_failed_video_still_blocks_delete_while_siblings_are_active(self):
        self.chunks(2)
        token = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker").processing_token
        fail_work(self.db, VideoSearchChunk, "v1", 0, token, PermanentProcessingError("bad media"))
        self.assertEqual(self.db.get(Video, "v1").status, "failed")
        self.assertTrue(has_active_work(self.db, "v1"))
        video = self.db.get(Video, "v1")
        video.status = "deleting"
        self.db.commit()
        self.assertIsNone(claim_work(self.db, VideoSearchChunk, "v1", 1, "late-worker"))

    def test_wrapper_uses_persisted_attempts_even_when_celery_retries_is_zero(self):
        self.chunks()
        work_query(self.db, VideoSearchChunk, "v1", 0).update({"processing_attempt_count": 2})
        self.db.commit()
        task = SimpleNamespace(request=SimpleNamespace(hostname="worker", retries=0))
        with patch("app.workers.durable_task.create_session", self.Session):
            with self.assertRaises(TemporaryStorageError):
                run_durable_task(task, "v1", "chunk", Mock(side_effect=TemporaryStorageError("S3 down")), chunk_index=0)
        self.db.expire_all()
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "failed")

    def test_failure_recording_outage_is_recovered_by_lease(self):
        self.chunks()
        task = SimpleNamespace(request=SimpleNamespace(hostname="worker", retries=0))
        with patch("app.workers.durable_task.create_session", self.Session), \
             patch("app.workers.durable_task.fail_work", side_effect=RuntimeError("database lost")):
            with self.assertRaisesRegex(RuntimeError, "database lost"):
                run_durable_task(task, "v1", "chunk", Mock(side_effect=TemporaryStorageError("S3 down")), chunk_index=0)
        self.db.expire_all()
        self.expire(VideoSearchChunk, 0)
        recover_expired(self.db)
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "retrying")

    def finalizer_fixture(self):
        self.chunks()
        token = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker").processing_token
        paths = {"transcript_path": "chunk.txt", "segments_path": "segments.json", "embedding_path": "embeddings.json"}
        complete_work(self.db, VideoSearchChunk, "v1", 0, token, paths)
        storage = MemoryStorage()
        storage.objects.update({"chunk.txt": "hello", "segments.json": [{"start": 1, "end": 2, "text": "hello"}], "embeddings.json": []})
        return storage

    def run_finalizer(self, storage):
        from app.services.video_service import finalize_chunked_search_if_ready
        task = SimpleNamespace(request=SimpleNamespace(hostname="finalizer", retries=0))
        with patch("app.workers.durable_task.create_session", self.Session), \
             patch("app.services.video_service.create_session", self.Session), \
             patch("app.services.processing_lease.create_session", self.Session), \
             patch("app.services.video_service.get_storage", return_value=storage):
            return run_durable_task(task, "v1", "finalize", finalize_chunked_search_if_ready)

    def test_real_finalizer_retries_s3_failure_without_retranscribing(self):
        storage = self.finalizer_fixture()
        storage.fail_final_write = True
        self.assertEqual(self.run_finalizer(storage)["status"], "retrying")
        self.db.expire_all()
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "completed")
        self.assertNotEqual(self.db.get(Video, "v1").embedding_status, "completed")
        storage.fail_final_write = False
        self.due(PipelineJob, "finalize")
        self.assertEqual(self.run_finalizer(storage)["status"], "processed")
        self.db.expire_all()
        video = self.db.get(Video, "v1")
        self.assertEqual(video.embedding_status, "completed")
        self.assertEqual(storage.read_text(video.transcript_path), "hello")
        self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).processing_attempt_count, 1)

    def test_late_finalizer_cannot_publish_video_paths(self):
        storage = self.finalizer_fixture()

        def reassign():
            self.expire(PipelineJob, "finalize")
            recover_expired(self.db)
            self.due(PipelineJob, "finalize")
            claim_work(self.db, PipelineJob, "v1", "finalize", "replacement")

        storage.on_final_write = reassign
        self.assertEqual(self.run_finalizer(storage)["status"], "stale")
        self.db.expire_all()
        self.assertIsNone(self.db.get(Video, "v1").transcript_path)
        self.assertEqual(self.db.get(PipelineJob, ("v1", "finalize")).processing_owner, "replacement")

    def test_final_readiness_commits_with_finalizer_success(self):
        storage = self.finalizer_fixture()
        self.db.get(Video, "v1").processed_path = "processed/video.mp4"
        self.db.commit()
        with patch("app.services.video_service.record_full_processing_if_ready", side_effect=RuntimeError("crash after commit")):
            self.run_finalizer(storage)
        self.db.expire_all()
        self.assertEqual(self.db.get(Video, "v1").status, "processed")
        self.assertEqual(self.db.get(PipelineJob, ("v1", "finalize")).status, "completed")

    def test_upload_creates_and_deletion_revokes_search_projections(self):
        from app.db.models import SearchProjection, SearchRevision, SearchDeletion
        with patch.dict(os.environ, {"SEARCH_PROJECTIONS_ENABLED": "true", "SEARCH_EMBEDDING_REVISION": ""}):
            self.test_upload_through_search_playback_and_deletion()
        self.db.expire_all()
        self.assertEqual(self.db.query(SearchRevision).count(), 1)
        projection = self.db.query(SearchProjection).one()
        self.assertEqual(projection.status, "obsolete")
        self.assertIsNone(projection.accepted_attempt_id)
        self.assertIsNotNone(self.db.get(SearchDeletion, projection.video_id))

    def test_upload_through_search_playback_and_deletion(self):
        from fastapi import UploadFile
        from qdrant_client import QdrantClient
        from app.services import video_service

        storage = MemoryStorage()
        with patch("app.services.storage_service.S3Storage", return_value=storage):
            from app.api import videos as api
        client = QdrantClient(":memory:")
        self.addCleanup(client.close)
        task = SimpleNamespace(request=SimpleNamespace(hostname="test-worker"))
        user = SimpleNamespace(user_id="owner", email="owner@example.test", name="Owner", picture_url=None)
        vector = [1.0] + [0.0] * 383
        whisper = Mock()
        whisper.transcribe.return_value = {"segments": [{"start": 7, "end": 9, "text": "hello world"}]}

        def media_command(args, **kwargs):
            Path(args[-1]).write_bytes(b"generated media")

        with patch.object(api, "storage", storage), \
             patch("app.workers.durable_task.create_session", self.Session), \
             patch("app.services.processing_lease.create_session", self.Session), \
             patch.object(video_service, "create_session", self.Session), \
             patch.object(video_service, "get_storage", return_value=storage), \
             patch.object(video_service, "get_ffmpeg_path", return_value="ffmpeg"), \
             patch.object(video_service, "probe_duration_seconds", return_value=610), \
             patch.object(video_service.subprocess, "run", side_effect=media_command), \
             patch.object(video_service, "get_whisper_model", return_value=whisper), \
             patch.object(video_service, "get_embedding_model"), \
             patch.object(video_service, "build_segment_embeddings", side_effect=lambda segments: [dict(segment, embedding=vector) for segment in segments]), \
             patch("app.services.qdrant_service.get_qdrant_client", return_value=client), \
             patch.object(api, "encode_text", return_value=vector):
            upload = api.upload_videos(file=UploadFile(filename="lecture.mp4", file=BytesIO(b"raw media")),
                title="Lecture", visibility="private", current_user=user, db=self.db)
            video_id = upload["video_id"]
            self.assertEqual(self.db.query(PipelineJob).filter_by(video_id=video_id).count(), 2)
            run_durable_task(task, video_id, "search", video_service.prepare_video_search_chunks)
            self.assertEqual(run_durable_task(task, video_id, "search", video_service.prepare_video_search_chunks)["status"], "skipped")
            self.db.expire_all()
            chunks = self.db.query(VideoSearchChunk).filter_by(video_id=video_id).all()
            self.assertEqual(len(chunks), 3)
            for index in range(3):
                run_durable_task(task, video_id, "chunk", video_service.process_video_search_chunk, chunk_index=index)
            run_durable_task(task, video_id, "finalize", video_service.finalize_chunked_search_if_ready)
            self.db.expire_all()
            self.assertEqual(self.db.get(Video, video_id).embedding_status, "completed")
            self.assertIsNone(self.db.get(Video, video_id).processed_path)
            result = api.search(video_id, api.SearchRequest(query="hello"), current_user=user, db=self.db)
            self.assertEqual(len(result["results"]), 3)
            run_durable_task(task, video_id, "playback", video_service.process_video_playback_artifacts)
            self.db.expire_all()
            self.assertEqual(self.db.get(Video, video_id).status, "processed")
            self.assertIn(self.db.get(Video, video_id).thumbnail_path, storage.objects)
            self.assertEqual(api.delete_video_endpoint(video_id, current_user=user, db=self.db)["status"], "deleted")
            self.assertIsNone(self.db.get(Video, video_id))
            self.assertEqual(self.db.query(PipelineJob).filter_by(video_id=video_id).count(), 0)
            self.assertFalse(any(video_id in key for key in storage.objects))


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Set VINDEX_TEST_DATABASE_URL for isolated Postgres tests")
class PostgresDurablePipelineTest(DurablePipelineTest):
    database_url = os.getenv("VINDEX_TEST_DATABASE_URL", "sqlite:///:memory:")

    def test_concurrent_claims_have_exactly_one_winner(self):
        self.chunks()
        barrier = Barrier(2)

        def claim(owner):
            db = self.Session()
            try:
                barrier.wait(timeout=10)
                row = claim_work(db, VideoSearchChunk, "v1", 0, owner)
                return row.processing_token if row else None
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(claim, ["worker-a", "worker-b"]))
        self.assertEqual(sum(token is not None for token in winners), 1)

    def test_killed_process_leaves_work_recoverable(self):
        self.chunks()
        process = subprocess.Popen([sys.executable, "-m", "tests.lease_crash_worker"],
            env={**os.environ, "VINDEX_TEST_SCHEMA": self.schema},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 30
            token = None
            while time.monotonic() < deadline:
                self.db.expire_all()
                row = self.db.get(VideoSearchChunk, ("v1", 0))
                token = row.processing_token
                self.db.rollback()
                if token:
                    break
                if process.poll() is not None:
                    self.fail(process.communicate(timeout=5)[1])
                time.sleep(0.1)
            self.assertIsNotNone(token, "Worker never claimed the chunk")
            process.kill()
            process.communicate(timeout=10)
            self.expire(VideoSearchChunk, 0)
            recover_expired(self.db)
            self.due(VideoSearchChunk, 0)
            replacement = claim_work(self.db, VideoSearchChunk, "v1", 0, "replacement")
            self.assertNotEqual(replacement.processing_token, token)
            complete_work(self.db, VideoSearchChunk, "v1", 0, replacement.processing_token)
            self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).status, "completed")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    @unittest.skipUnless(os.getenv("VINDEX_TEST_REDIS_URL"), "Set VINDEX_TEST_REDIS_URL for real Celery delivery")
    def test_real_celery_dispatch_and_duplicate_delivery(self):
        from celery import Celery
        self.chunks()
        redis_url = os.environ["VINDEX_TEST_REDIS_URL"]
        app = Celery("pipeline-smoke", broker=redis_url, backend=redis_url)
        process = subprocess.Popen([
            sys.executable, "-m", "celery", "-A", "tests.pipeline_smoke_app:app",
            "worker", "--pool=solo", "--loglevel=WARNING", "-Q", "search",
            "--without-heartbeat", "--without-gossip", "--without-mingle",
        ], env={**os.environ, "DATABASE_URL": self.database_url, "PGOPTIONS": f"-csearch_path={self.schema}",
                "CELERY_BROKER_URL": redis_url, "CELERY_RESULT_BACKEND": redis_url},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            messages = []

            def publish(name, **kwargs):
                messages.append(app.send_task(name, **kwargs))

            self.assertEqual(dispatch_pending(self.db, publish), 1)
            self.assertEqual(messages[0].get(timeout=120)["status"], "processed")
            duplicate = app.send_task("process_video_search_chunk_task", args=["v1", 0], queue="search")
            self.assertEqual(duplicate.get(timeout=20)["status"], "skipped")
            self.db.expire_all()
            self.assertEqual(self.db.get(VideoSearchChunk, ("v1", 0)).processing_attempt_count, 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
            app.close()
