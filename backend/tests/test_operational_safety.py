from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
import errno
import os
import subprocess
import sys
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from qdrant_client import QdrantClient

from app.db.models import PipelineJob, UploadRequest, Video, VideoSearchChunk
from app.db.pipeline_repository import claim_work, complete_work, renew_work
from app.db.upload_repository import commit_upload, reserve_upload, release_upload, upload_fingerprint
from app.services.maintenance_service import artifact_protected, collect_artifacts, repair_indexes, record_operation
from app.services.media_service import run_media_command
from app.services.metrics_service import update_metrics
from app.services.processing_errors import classify_processing_exception, StaleProcessingAttempt
from app.services.qdrant_service import COLLECTION_NAME, upsert_segments, search_segments
from app.workers.maintenance import operational_metrics
from app.workers.pipeline_dispatcher import recover_expired
from tests import test_durable_pipeline as durable

MemoryStorage = durable.MemoryStorage


class OperationalSafetyTest(unittest.TestCase):
    database_url = "sqlite:///:memory:"
    setUp = durable.DurablePipelineTest.setUp
    cleanup = durable.DurablePipelineTest.cleanup
    chunks = durable.DurablePipelineTest.chunks

    def reserve(self, owner="u1", key="intent-1", fingerprint="hash"):
        return reserve_upload(self.db, owner, key, fingerprint, ".mp4")

    def commit(self, row):
        response = {"video_id": row.video_id, "status": "uploaded"}
        commit_upload(self.db, row.owner_user_id, row.idempotency_key, row.processing_token,
            response, {"video_id": row.video_id, "owner_user_id": row.owner_user_id,
                       "raw_path": row.raw_path, "status": "uploaded"})
        return response

    def test_upload_replay_has_one_video_and_one_set_of_jobs(self):
        row, _ = self.reserve()
        response = self.commit(row)
        self.assertEqual(self.reserve(), (None, response))
        self.assertEqual(self.db.query(Video).count(), 2)
        self.assertEqual(self.db.query(PipelineJob).count(), 2)

    def test_upload_rejects_changed_content_and_inflight_duplicate(self):
        self.reserve()
        for fingerprint in ("hash", "different"):
            with self.assertRaises(HTTPException) as raised:
                self.reserve(fingerprint=fingerprint)
            self.assertEqual(raised.exception.status_code, 409)

    def test_upload_key_is_owner_scoped_and_deleted_upload_stays_deleted(self):
        row, _ = self.reserve()
        response = self.commit(row)
        second, _ = self.reserve(owner="u2")
        self.assertNotEqual(second.video_id, response["video_id"])
        self.db.query(PipelineJob).filter_by(video_id=response["video_id"]).delete()
        self.db.query(Video).filter_by(video_id=response["video_id"]).delete()
        self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            self.reserve()
        self.assertEqual(raised.exception.status_code, 410)

    def test_failed_upload_transaction_can_retry_immediately(self):
        row, _ = self.reserve()
        old_token, video_id = row.processing_token, row.video_id
        with patch("app.db.video_repository.add_job", side_effect=RuntimeError("Database failure")):
            with self.assertRaises(RuntimeError):
                self.commit(row)
        self.assertIsNone(self.db.get(Video, video_id))
        retry, _ = self.reserve()
        self.assertEqual(retry.video_id, video_id)
        self.assertNotEqual(retry.processing_token, old_token)
        self.commit(retry)

    def test_late_upload_attempt_cannot_publish_or_release_new_owner(self):
        row, _ = self.reserve()
        old_token, video_id = row.processing_token, row.video_id
        release_upload(self.db, "u1", "intent-1", old_token)
        retry, _ = self.reserve()
        new_token = retry.processing_token
        with self.assertRaises(HTTPException):
            commit_upload(self.db, "u1", "intent-1", old_token, {}, {"video_id": video_id})
        self.assertEqual(self.db.get(UploadRequest, ("u1", "intent-1")).processing_token, new_token)
        self.assertIsNone(self.db.get(Video, video_id))
        self.commit(retry)

    def test_fingerprint_validates_bytes_and_metadata_before_storage(self):
        options = dict(filename="a.mp4", title="a", visibility="private", max_bytes=4)
        stream = BytesIO(b"1234")
        first = upload_fingerprint(stream, **options)
        self.assertEqual(stream.tell(), 0)
        self.assertNotEqual(first, upload_fingerprint(BytesIO(b"4321"), **options))
        with self.assertRaises(HTTPException) as raised:
            upload_fingerprint(BytesIO(b"12345"), **options)
        self.assertEqual(raised.exception.status_code, 413)

    def test_heartbeats_cannot_extend_absolute_deadline(self):
        self.chunks()
        row = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker")
        token, deadline = row.processing_token, row.deadline_at
        renew_work(self.db, VideoSearchChunk, "v1", 0, token, progress=True)
        self.db.refresh(row)
        self.assertEqual(row.deadline_at, deadline)
        row.deadline_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(StaleProcessingAttempt):
            renew_work(self.db, VideoSearchChunk, "v1", 0, token)
        self.assertEqual(recover_expired(self.db), 1)
        self.db.refresh(row)
        self.assertEqual(row.status, "retrying")
        self.assertEqual(row.last_error_code, "processing_deadline_exceeded")

    def test_gc_preserves_current_raw_and_removes_abandoned_attempt(self):
        storage, qdrant = MemoryStorage(), QdrantClient(":memory:")
        self.addCleanup(qdrant.close)
        row, _ = self.reserve()
        old_path, old_token = row.raw_path, row.processing_token
        storage.objects[old_path] = b"old"
        self.assertEqual(collect_artifacts(self.db, storage, qdrant)["deleted_objects"], 0)
        release_upload(self.db, "u1", "intent-1", old_token)
        row, _ = self.reserve()
        accepted_path = row.raw_path
        storage.objects[accepted_path] = b"accepted"
        storage.objects["raw_videos/not-a-video/unknown"] = b"unrecognized"
        self.commit(row)
        self.assertEqual(collect_artifacts(self.db, storage, qdrant)["deleted_objects"], 1)
        self.assertNotIn(old_path, storage.objects)
        self.assertIn(accepted_path, storage.objects)
        self.assertIn("raw_videos/not-a-video/unknown", storage.objects)

    def test_gc_protects_active_and_accepted_attempts(self):
        self.chunks()
        row = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker")
        token = row.processing_token
        self.assertTrue(artifact_protected(self.db, "v1", token))
        self.db.rollback()
        complete_work(self.db, VideoSearchChunk, "v1", 0, token)
        self.assertTrue(artifact_protected(self.db, "v1", token))
        self.assertFalse(artifact_protected(self.db, "v1", str(uuid4())))

    def test_gc_leaves_recent_orphans_for_the_grace_period(self):
        storage, qdrant = MemoryStorage(), QdrantClient(":memory:")
        self.addCleanup(qdrant.close)
        key = f"processed_videos/{uuid4()}/{uuid4()}/video.mp4"
        storage.objects[key] = b"late write"
        def scan(prefix, cursor, limit):
            return ([{"Key": key, "LastModified": datetime.now().astimezone()}] if key.startswith(prefix) else []), None
        with patch.object(storage, "scan_objects", side_effect=scan):
            self.assertEqual(collect_artifacts(self.db, storage, qdrant)["deleted_objects"], 0)
        self.assertIn(key, storage.objects)

    def test_one_broken_index_does_not_starve_other_videos(self):
        self.db.get(Video, "v1").embedding_status = "completed"
        self.db.add(Video(video_id="v2", embedding_status="completed", status="processed"))
        self.db.commit()
        with patch("app.services.maintenance_service.repair_video_index", side_effect=[RuntimeError("missing artifact"), 1]) as repair, \
             self.assertLogs("app.services.maintenance_service", level="ERROR"):
            result = repair_indexes(self.db, MemoryStorage(), None, limit=2)
        self.assertEqual(repair.call_count, 2)
        self.assertEqual(result["repaired_chunks"], 1)
        self.assertEqual(result["cursor"], "v2")
        self.assertEqual(result["errors"][0]["video_id"], "v1")

    def test_missing_vectors_are_rebuilt_from_accepted_embeddings(self):
        storage, qdrant = MemoryStorage(), QdrantClient(":memory:")
        self.addCleanup(qdrant.close)
        self.chunks()
        row = claim_work(self.db, VideoSearchChunk, "v1", 0, "worker")
        token = row.processing_token
        embeddings = [{"segment_id": "0:0", "chunk_index": 0, "start": 0, "end": 1,
                       "text": "accepted", "embedding": [1.0] + [0.0] * 383}]
        storage.objects["accepted.json"] = embeddings
        complete_work(self.db, VideoSearchChunk, "v1", 0, token,
            {"embedding_path": "accepted.json", "segment_count": 1})
        self.db.get(Video, "v1").embedding_status = "completed"
        self.db.commit()
        with patch("app.services.qdrant_service.get_qdrant_client", return_value=qdrant):
            self.assertEqual(repair_indexes(self.db, storage, qdrant)["repaired_chunks"], 1)
            self.assertEqual(repair_indexes(self.db, storage, qdrant)["repaired_chunks"], 0)
            self.assertEqual(search_segments(embeddings[0]["embedding"], "v1", chunk_attempts=[(0, token)])[0]["text"], "accepted")
            stale = str(uuid4())
            with patch("app.services.qdrant_service.time", return_value=0):
                upsert_segments("v1", embeddings, attempt_token=stale)
            self.assertEqual(collect_artifacts(self.db, storage, qdrant)["deleted_vectors"], 1)
            self.assertEqual(qdrant.count(COLLECTION_NAME).count, 1)

    def test_monitoring_reports_missing_dispatcher_and_stuck_work(self):
        self.chunks()
        row = self.db.get(VideoSearchChunk, ("v1", 0))
        row.created_at = datetime.utcnow() - timedelta(minutes=10)
        self.db.commit()
        values = operational_metrics(self.db)
        self.assertGreater(values["pending_age_seconds"], 590)
        self.assertEqual(values["dispatcher_error"], 1)
        record_operation(self.db, "dispatcher")
        self.assertLess(operational_metrics(self.db)["dispatcher_success_age_seconds"], 5)


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Requires isolated Postgres")
class PostgresOperationalSafetyTest(OperationalSafetyTest):
    database_url = os.getenv("VINDEX_TEST_DATABASE_URL", "sqlite:///:memory:")

    def test_simultaneous_upload_reservations_have_one_owner(self):
        barrier = Barrier(2)
        def reserve():
            with self.Session() as db:
                barrier.wait(timeout=10)
                try:
                    row, _ = reserve_upload(db, "u1", "same-key", "hash", ".mp4")
                    return row.processing_token
                except HTTPException as exc:
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(self.db.query(UploadRequest).count(), 1)


class ExternalSafetyTest(unittest.TestCase):
    def test_wrapped_s3_transfer_errors_keep_their_classification(self):
        for status in (403, 503):
            try:
                try:
                    raise ClientError({"Error": {"Code": str(status)}, "ResponseMetadata": {"HTTPStatusCode": status}}, "PutObject")
                except ClientError:
                    raise S3UploadFailedError("Transfer failed")
            except S3UploadFailedError as exc:
                self.assertEqual(classify_processing_exception(exc).retryable, status == 503)

    def test_metrics_conflict_reloads_and_preserves_both_updates(self):
        storage = MemoryStorage()
        original = storage.write_json_conditional
        injected = False
        def conflict(key, value, etag):
            nonlocal injected
            if not injected:
                injected = True
                update_metrics(storage, "v1", "playback", {"seconds": 5})
            return original(key, value, etag)
        with patch.object(storage, "write_json_conditional", side_effect=conflict):
            update_metrics(storage, "v1", "search", {"seconds": 10})
        result = storage.objects["metrics/v1.json"]
        self.assertEqual(result["playback"]["seconds"], 5)
        self.assertEqual(result["search"]["seconds"], 10)

    def test_media_command_really_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_media_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1, check=True)

    def test_resources_and_timeouts_retry_but_corrupt_media_does_not(self):
        for error in (MemoryError(), OSError(errno.ENOSPC, "full"), RuntimeError("CUDA out of memory"),
                      subprocess.TimeoutExpired("ffmpeg", 1), subprocess.CalledProcessError(137, "ffmpeg")):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(classify_processing_exception(error).retryable)
        self.assertFalse(classify_processing_exception(subprocess.CalledProcessError(1, "ffmpeg", stderr=b"invalid data")).retryable)
