import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, OperationState, Video
from app.services.elasticsearch_service import IndexNotReady
from app.services.lexical_index import LexicalIndex
from app.services.lexical_reconciliation import reconcile_lexical_index


def passage(text="rareidentifier", start=0):
    return {"start": start, "end": start + 1, "text": text}


class LexicalIndexTest(unittest.TestCase):
    def setUp(self):
        self.client = LexicalIndex("http://localhost:9200", "vindex-unit")
        self.addCleanup(self.client.close)

    def test_partial_bulk_is_not_published_and_ids_are_deterministic(self):
        with patch.object(self.client, "ensure_index"), \
             patch.object(self.client, "manifest", side_effect=IndexNotReady), \
             patch.object(self.client, "request", side_effect=[{}, {"errors": True}]) as call:
            with self.assertRaises(RuntimeError):
                self.client.prepare("a", "r1", [passage()])
            self.assertEqual(call.call_count, 2)
            self.assertIn(self.client.revision_token("a", "r1") + "-0", call.call_args.kwargs["data"])

    def test_search_filters_video_revision_and_passage_kind(self):
        with patch.object(self.client, "manifest", return_value={"ready": True}), \
             patch.object(self.client, "request", return_value={"hits": {"hits": []}}) as call:
            self.assertEqual(self.client.search("private", "accepted", "rare"), [])
            query = call.call_args.kwargs["json"]["query"]["bool"]
            self.assertIn({"term": {"video_id": "private"}}, query["filter"])
            self.assertIn({"term": {"revision_token": self.client.revision_token("private", "accepted")}}, query["filter"])
            self.assertIn({"term": {"kind": "passage"}}, query["filter"])

    def test_incomplete_and_timed_out_search_are_not_successful(self):
        with patch.object(self.client, "manifest", return_value={"ready": False}):
            with self.assertRaises(IndexNotReady):
                self.client.search("a", "r", "q")
        with patch.object(self.client, "manifest", return_value={"ready": True}), \
             patch.object(self.client, "request", return_value={"timed_out": True}):
            with self.assertRaises(RuntimeError):
                self.client.search("a", "r", "q")

    def test_delete_partial_failure_is_retryable(self):
        with patch.object(self.client, "request", return_value={"version_conflicts": 1}):
            with self.assertRaises(RuntimeError):
                self.client.delete("a")

    def test_index_configuration_cannot_contain_wildcard_or_path(self):
        for name in ["*", "a,b", "a/_search", "_all"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                LexicalIndex("http://localhost:9200", name)


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.storage = Mock()
        self.storage.read_json.return_value = [passage()]
        self.elastic = Mock()
        self.elastic.revision_token.side_effect = LexicalIndex.revision_token
        self.elastic.revisions.return_value = ([], None)

    def add(self, video_id, **kwargs):
        self.db.add(Video(video_id=video_id, status=kwargs.get("status", "processed"),
            segments_status="completed", segments_path=kwargs.get("revision", "r1")))
        self.db.commit()

    def test_backfill_continues_after_failure_and_persists_bounded_cursor(self):
        for name in ["a", "b", "c"]:
            self.add(name)
        self.elastic.prepare.side_effect = [RuntimeError("outage"), None]
        result = reconcile_lexical_index(self.db, self.storage, self.elastic, limit=2)
        self.assertEqual(result["checked_videos"], 1)
        self.assertEqual(result["cursor"], "b")
        self.assertEqual(result["errors"][0]["video_id"], "a")
        self.db.add(OperationState(name="lexical_index", details=result))
        self.db.commit()
        self.elastic.prepare.side_effect = None
        result = reconcile_lexical_index(self.db, self.storage, self.elastic, limit=2)
        self.assertEqual(result["cursor"], "")
        self.assertEqual(self.elastic.prepare.call_args.args[:2], ("c", "r1"))

    def test_delete_during_indexing_removes_late_output(self):
        self.add("a")
        def delete_during_prepare(*_):
            self.db.get(Video, "a").status = "deleting"
            self.db.commit()
        self.elastic.prepare.side_effect = delete_during_prepare
        result = reconcile_lexical_index(self.db, self.storage, self.elastic)
        self.elastic.delete.assert_called_once_with("a", "r1")
        self.assertEqual(result["checked_videos"], 0)

    def test_cleanup_preserves_current_revision_and_removes_old_and_deleted(self):
        self.add("a")
        current = LexicalIndex.revision_token("a", "r1")
        old = LexicalIndex.revision_token("a", "r0")
        self.elastic.revisions.return_value = ([
            {"video_id": "a", "revision_token": current},
            {"video_id": "a", "revision_token": old},
            {"video_id": "gone", "revision_token": "deleted"}], None)
        result = reconcile_lexical_index(self.db, self.storage, self.elastic)
        self.assertEqual(result["removed_revisions"], 2)
        self.elastic.delete.assert_any_call("a", token=old)
        self.elastic.delete.assert_any_call("gone", token="deleted")


class LexicalApiTest(unittest.TestCase):
    def setUp(self):
        with patch("app.services.storage_service.S3Storage"):
            from app.api import videos
        self.api = videos
        self.video = SimpleNamespace(video_id="a", visibility="private", owner_user_id="owner",
            status="processed", segments_status="completed", segments_path="r1", embedding_status="completed")
        self.user = SimpleNamespace(user_id="owner")
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ELASTICSEARCH_URL": "http://localhost:9200"}).start()
        self.elastic = patch.object(videos, "ElasticsearchSearch").start().return_value
        self.elastic.search.return_value = [passage()]

    def search(self):
        return self.api.search("a", self.api.SearchRequest(query="q", algorithm="elasticsearch_bm25"),
                               current_user=self.user, db=Mock())

    def test_access_revoked_during_search_does_not_return_text(self):
        from fastapi import HTTPException
        revoked = SimpleNamespace(**dict(vars(self.video), owner_user_id="other"))
        with patch.object(self.api, "get_video", side_effect=[self.video, revoked]):
            with self.assertRaises(HTTPException) as caught:
                self.search()
        self.assertEqual(caught.exception.status_code, 404)

    def test_changed_revision_requires_retry(self):
        from fastapi import HTTPException
        changed = SimpleNamespace(**dict(vars(self.video), segments_path="r2"))
        with patch.object(self.api, "get_video", side_effect=[self.video, changed]):
            with self.assertRaises(HTTPException) as caught:
                self.search()
        self.assertEqual(caught.exception.status_code, 409)

    def test_outage_returns_503_and_closes_client(self):
        from fastapi import HTTPException
        self.elastic.search.side_effect = requests.ConnectionError()
        with patch.object(self.api, "get_video", return_value=self.video):
            with self.assertRaises(HTTPException) as caught:
                self.search()
        self.assertEqual(caught.exception.status_code, 503)
        self.elastic.close.assert_called_once()


@unittest.skipUnless(os.getenv("VINDEX_TEST_ELASTICSEARCH_URL"), "Requires Elasticsearch test URL")
class RealLexicalIndexTest(unittest.TestCase):
    def setUp(self):
        self.client = LexicalIndex(os.environ["VINDEX_TEST_ELASTICSEARCH_URL"], "vindex-test-" + uuid4().hex)
        self.addCleanup(self.client.close)
        self.addCleanup(self.remove_index)

    def remove_index(self):
        try:
            self.client.request("DELETE", self.client.index)
        except IndexNotReady:
            pass

    def test_replay_revision_isolation_repair_and_video_deletion(self):
        corpus = [passage(), passage("ordinary", 2)]
        token = self.client.prepare("a", "r1", corpus)
        self.assertEqual(token, self.client.prepare("a", "r1", corpus))
        self.client.prepare("b", "r1", [passage("rareidentifier private")])
        self.client.prepare("a", "r2", [passage("replacement")])
        self.assertEqual(self.client.search("a", "r1", "rareidentifier")[0]["text"], "rareidentifier")
        self.assertEqual(self.client.search("a", "r2", "rareidentifier"), [])
        with self.assertRaises(ValueError):
            self.client.prepare("a", "r1", [passage("changed")])
        self.client.request("DELETE", f"{self.client.index}/_doc/{token}-0", params={"refresh": "true"})
        self.client.prepare("a", "r1", corpus)
        self.assertEqual(len(self.client.search("a", "r1", "rareidentifier")), 1)
        groups, _ = self.client.revisions(limit=20)
        self.assertEqual(len(groups), 3)
        self.client.delete("a")
        self.assertEqual(self.client.search("b", "r1", "private")[0]["text"], "rareidentifier private")
        with self.assertRaises(IndexNotReady):
            self.client.search("a", "r1", "rareidentifier")

    def test_partial_bulk_recovers_with_same_revision_and_empty_transcript(self):
        original = self.client.request
        def fail_bulk(method, path, **kwargs):
            if path.endswith("/_bulk"):
                return {"errors": True}
            return original(method, path, **kwargs)
        with patch.object(self.client, "request", side_effect=fail_bulk):
            with self.assertRaises(RuntimeError):
                self.client.prepare("a", "r", [passage()])
        with self.assertRaises(IndexNotReady):
            self.client.search("a", "r", "rareidentifier")
        self.client.prepare("a", "r", [passage()])
        self.assertEqual(len(self.client.search("a", "r", "rareidentifier")), 1)
        self.client.prepare("empty", "r", [])
        self.assertEqual(self.client.search("empty", "r", "q"), [])
