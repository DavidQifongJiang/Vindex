"""Direct browser-to-S3 uploads and the public video response contract."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.schemas import CreateUploadRequest
from app.db.models import PipelineJob, UploadRequest, Video
from tests import test_durable_pipeline as durable

PRIVATE_FIELDS = {"raw_path", "processed_path", "audio_path", "transcript_path", "segments_path",
                  "embedding_path", "thumbnail_path", "processing_token", "processing_owner",
                  "last_error_message", "last_error_code", "owner_user_id", "current_search_revision_id"}


class UploadStorage(durable.MemoryStorage):
    def presigned_upload(self, key, *, content_type, size_bytes, seconds):
        self.last_policy = {"key": key, "content_type": content_type, "size_bytes": size_bytes, "seconds": seconds}
        return {"url": "https://bucket.example.test/", "fields": {"key": key, "Content-Type": content_type}}

    def object_size(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def copy_object(self, source_key, target_key):
        self.objects[target_key] = self.objects[source_key]


class DirectUploadTest(unittest.TestCase):
    database_url = "sqlite:///:memory:"
    setUp_db = durable.DurablePipelineTest.setUp
    cleanup = durable.DurablePipelineTest.cleanup

    def setUp(self):
        self.setUp_db()
        self.storage = UploadStorage()
        with patch("app.services.storage_service.S3Storage", return_value=self.storage):
            from app.api import videos as api
        self.api = api
        patcher = patch.object(api, "storage", self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = SimpleNamespace(user_id="owner", email="owner@example.test", name="Owner", picture_url=None)

    def request(self, **overrides):
        values = {"filename": "lecture.mp4", "title": "Lecture", "visibility": "private",
                  "size_bytes": 9, "content_type": "video/mp4"} | overrides
        return CreateUploadRequest(**values)

    def create(self, key="intent-1", **overrides):
        return self.api.create_direct_upload(self.request(**overrides), current_user=self.user,
                                             db=self.db, idempotency_key=key)

    def complete(self, video_id, key="intent-1"):
        return self.api.complete_direct_upload(video_id, current_user=self.user, db=self.db, idempotency_key=key)

    def put_object(self, created, content=b"raw media"):
        self.storage.objects[created["upload"]["fields"]["key"]] = content

    def test_upload_goes_straight_to_s3_then_completion_queues_processing_once(self):
        created = self.create()
        key = created["upload"]["fields"]["key"]
        self.assertTrue(key.startswith(f"raw_videos/{created['video_id']}/"))
        self.assertEqual(self.storage.last_policy["size_bytes"], 9)
        self.assertIsNone(self.db.get(Video, created["video_id"]))

        with self.assertRaises(HTTPException) as early:
            self.complete(created["video_id"])
        self.assertEqual(early.exception.status_code, 409)

        self.put_object(created)
        accepted = self.complete(created["video_id"])
        self.assertEqual(accepted["status"], "uploaded")
        video = self.db.get(Video, created["video_id"])
        self.assertEqual((video.owner_user_id, video.title), ("owner", "Lecture"))
        self.assertTrue(video.raw_path.startswith(f"raw_videos/{video.video_id}/"))
        self.assertNotEqual(video.raw_path, key)
        self.assertEqual(self.storage.objects[video.raw_path], b"raw media")
        self.assertNotIn(key, self.storage.objects)
        self.assertEqual(self.db.query(PipelineJob).filter_by(video_id=video.video_id).count(), 2)

        self.assertEqual(self.complete(created["video_id"]), accepted)
        replay = self.create()
        self.assertIsNone(replay["upload"])
        self.assertEqual(replay["completed"].video_id, created["video_id"])
        self.assertEqual(self.db.query(Video).filter_by(owner_user_id="owner").count(), 1)
        self.assertEqual(self.db.query(PipelineJob).filter_by(video_id=video.video_id).count(), 2)

    def test_reusing_the_upload_url_after_completion_cannot_change_processing_input(self):
        created = self.create()
        upload_key = created["upload"]["fields"]["key"]
        self.put_object(created)
        self.complete(created["video_id"])
        raw_path = self.db.get(Video, created["video_id"]).raw_path
        # The presigned POST is still valid: same size and type, different bytes.
        self.storage.objects[upload_key] = b"evil data"
        self.assertEqual(self.storage.objects[raw_path], b"raw media")
        self.assertEqual(self.db.get(Video, created["video_id"]).raw_path, raw_path)
        from app.services.maintenance_service import object_identity
        self.assertEqual(object_identity(raw_path)[0], created["video_id"])

    def test_failed_commit_leaves_no_snapshot_and_can_retry(self):
        created = self.create()
        self.put_object(created)
        with patch.object(self.api, "commit_upload", side_effect=RuntimeError("database down")):
            with self.assertRaises(RuntimeError):
                self.complete(created["video_id"])
        upload_key = created["upload"]["fields"]["key"]
        self.assertEqual([k for k in self.storage.objects if k.startswith("raw_videos/")], [upload_key])
        accepted = self.complete(created["video_id"])
        self.assertEqual(accepted["video_id"], created["video_id"])

    def test_retrying_create_resumes_the_same_attempt(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first["video_id"], second["video_id"])
        self.assertEqual(first["upload"]["fields"]["key"], second["upload"]["fields"]["key"])

    def test_same_key_with_different_file_is_rejected(self):
        self.create()
        with self.assertRaises(HTTPException) as raised:
            self.create(size_bytes=10)
        self.assertEqual(raised.exception.status_code, 409)

    def test_size_mismatch_discards_object_and_releases_reservation(self):
        created = self.create()
        self.put_object(created, b"different length")
        with self.assertRaises(HTTPException) as raised:
            self.complete(created["video_id"])
        self.assertEqual(raised.exception.status_code, 422)
        self.assertFalse([k for k in self.storage.objects if k.startswith("raw_videos/")])
        retry = self.create()
        self.assertEqual(retry["video_id"], created["video_id"])
        self.assertNotEqual(retry["upload"]["fields"]["key"], created["upload"]["fields"]["key"])

    def test_rejects_non_video_oversized_and_unknown_upload(self):
        for overrides, status in (({"content_type": "text/html"}, 415),
                                  ({"size_bytes": self.api.MAX_UPLOAD_BYTES + 1}, 413),
                                  ({"filename": "clip.mp4;rm"}, 400)):
            with self.assertRaises(HTTPException) as raised:
                self.create(key=f"intent-{status}", **overrides)
            self.assertEqual(raised.exception.status_code, status)
        with self.assertRaises(HTTPException) as missing:
            self.complete("not-a-video", key="intent-x")
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(self.db.query(UploadRequest).count(), 0)

    def test_completion_requires_the_uploading_users_key(self):
        created = self.create()
        self.put_object(created)
        stranger = SimpleNamespace(user_id="stranger", email=None, name=None, picture_url=None)
        with self.assertRaises(HTTPException) as raised:
            self.api.complete_direct_upload(created["video_id"], current_user=stranger, db=self.db,
                                            idempotency_key="intent-1")
        self.assertEqual(raised.exception.status_code, 404)


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Requires isolated Postgres")
class PostgresDirectUploadTest(DirectUploadTest):
    """Row locks are no-ops on SQLite; run the reservation flow on real Postgres."""
    database_url = os.getenv("VINDEX_TEST_DATABASE_URL", "sqlite:///:memory:")


class VideoResponseContractTest(unittest.TestCase):
    database_url = "sqlite:///:memory:"
    setUp_db = durable.DurablePipelineTest.setUp
    cleanup = durable.DurablePipelineTest.cleanup

    def setUp(self):
        self.setUp_db()
        with patch("app.services.storage_service.S3Storage", return_value=durable.MemoryStorage()):
            from app.api import videos as api
        self.api = api
        video = self.db.get(Video, "v1")
        video.visibility, video.raw_path, video.processed_path = "public", "raw_videos/v1/t/v1.mp4", "processed_videos/v1/a.mp4"
        video.processing_token, video.error_message = "secret-token", "ffmpeg exited 1 at /tmp/x"
        self.db.commit()
        self.owner = SimpleNamespace(user_id="u1", email=None, name=None, picture_url=None)

    def dump(self, model):
        return model.model_dump() if hasattr(model, "model_dump") else model

    def test_status_never_exposes_storage_keys_or_tokens(self):
        for user in (None, self.owner):
            body = self.dump(self.api.get_video_status("v1", current_user=user, db=self.db))
            self.assertFalse(PRIVATE_FIELDS & set(body), PRIVATE_FIELDS & set(body))
            self.assertTrue(body["playable"])
        anonymous = self.dump(self.api.get_video_status("v1", current_user=None, db=self.db))
        owner = self.dump(self.api.get_video_status("v1", current_user=self.owner, db=self.db))
        self.assertEqual((anonymous["is_owner"], anonymous["error_message"]), (False, None))
        self.assertEqual((owner["is_owner"], owner["error_message"]), (True, "ffmpeg exited 1 at /tmp/x"))

    def test_lists_use_the_same_contract(self):
        public = self.api.get_public_videos(current_user=None, db=self.db)["videos"]
        mine = self.api.get_videos(current_user=self.owner, db=self.db)["videos"]
        for item in public + mine:
            self.assertFalse(PRIVATE_FIELDS & set(self.dump(item)))
        self.assertEqual([self.dump(item)["is_owner"] for item in mine], [True])


class DirectUploadHttpTest(DirectUploadTest):
    """The same flow through FastAPI, so response models and headers are exercised."""

    def setUp(self):
        from functools import partial
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        # Sync routes run in a threadpool; share one SQLite connection across threads.
        shared = partial(create_engine, poolclass=StaticPool, connect_args={"check_same_thread": False})
        with patch.object(durable, "create_engine", shared):
            super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.db.session import get_db
        from app.services.auth_service import get_current_user, get_optional_user
        app = FastAPI()
        app.include_router(self.api.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_optional_user] = lambda: None
        self.client = TestClient(app)

    def test_http_round_trip(self):
        headers = {"Idempotency-Key": "http-1"}
        body = {"filename": "talk.mov", "title": "Talk", "size_bytes": 9, "content_type": "video/quicktime"}
        created = self.client.post("/videos/uploads", json=body, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        upload = created.json()["upload"]
        self.assertEqual((upload["method"], upload["fields"]["Content-Type"]), ("POST", "video/quicktime"))
        self.storage.objects[upload["fields"]["key"]] = b"raw media"
        done = self.client.post(f"/videos/uploads/{created.json()['video_id']}/complete", headers=headers)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["filename"], f"{created.json()['video_id']}.mov")
        replay = self.client.post("/videos/uploads", json=body, headers=headers).json()
        self.assertEqual((replay["upload"], replay["completed"]["title"]), (None, "Talk"))
        status = self.client.get(f"/videos/{created.json()['video_id']}/status").json()
        self.assertFalse(PRIVATE_FIELDS & set(status))
        self.assertEqual(self.client.post(f"/videos/uploads/{created.json()['video_id']}/complete").status_code, 400)


if __name__ == "__main__":
    unittest.main()
