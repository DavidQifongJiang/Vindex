import asyncio
from concurrent.futures import Future
from datetime import timedelta
import os
from threading import Event
import unittest
from unittest.mock import patch, AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Video
from app.db.search_event_models import (SearchEventBudget as Budget, SearchEventContext as Context,
    SearchEventOutbox as Outbox, SearchEventProjection as Projection, SearchEventQuarantine as Quarantine)
from app.db import search_event_repository as repo
from app.services.search_events import completion, Interaction, SearchEvent, capture
from app.api.search import router as search_router, LibrarySearchRequest
from app.api.search_analytics import router as analytics_router
from app.services.auth_service import AuthUser, get_current_user


def event():
    return completion(str(uuid4()), "semantic", 200,
        {"results": [], "effective_mode": "semantic", "coverage": {"complete": True, "total_videos": 1}}, 12)


class SearchEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory.begin() as db:
            db.add(Budget(name="outbox", pending_bytes=0, expired_events=0))
        self.session_patch = patch.object(repo, "sessions", return_value=self.factory)
        self.session_patch.start()
        self.env = patch.dict(os.environ, {"SEARCH_EVENTS_SYNTHETIC_ONLY": "true",
            "SEARCH_EVENTS_CAPTURE_ENABLED": "true", "SEARCH_EVENTS_INTAKE_ENABLED": "true",
            "SEARCH_EVENTS_DASHBOARD_ENABLED": "true", "LIBRARY_SEARCH_ENABLED": "true",
            "SEARCH_EVENTS_REVIEWERS": "reviewer"})
        self.env.start()
        self.app = FastAPI()
        self.app.include_router(search_router)
        self.app.include_router(analytics_router)
        self.app.dependency_overrides[get_current_user] = lambda: AuthUser("owner")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.env.stop()
        self.session_patch.stop()
        self.engine.dispose()

    def test_default_is_semantic(self):
        from app.services.search_contracts import LibrarySearchQuery
        self.assertEqual(LibrarySearchRequest(query="heap").mode, "semantic")
        self.assertEqual(LibrarySearchQuery("heap").mode, "semantic")

    def test_atomic_capture_and_opt_in_query(self):
        e = event()
        repo.capture_search("owner", "private text", e, [], False)
        with self.factory() as db:
            self.assertIsNone(db.get(Context, e["search_id"]).query)
            self.assertNotIn("private text", str(db.get(Outbox, e["event_id"]).payload))
        with patch.dict(os.environ, {"SEARCH_EVENTS_OUTBOX_BYTES": "1"}):
            failed = event()
            with self.assertRaises(ValueError):
                repo.capture_search("owner", "secret", failed, [], True)
        with self.factory() as db:
            self.assertIsNone(db.get(Context, failed["search_id"]))

    def test_retry_conflict_owner_and_expiry(self):
        e = event()
        repo.capture_search("owner", "query", e, [], True)
        interaction = Interaction(event_id=str(uuid4()), event_type="search.feedback_submitted", feedback="not_helpful")
        repo.accept_interaction("owner", e["search_id"], interaction)
        repo.accept_interaction("owner", e["search_id"], interaction)
        with self.assertRaises(PermissionError):
            repo.accept_interaction("other", e["search_id"], interaction)
        with self.assertRaises(ValueError):
            repo.accept_interaction("owner", e["search_id"], interaction.model_copy(update={"feedback": "helpful"}))
        with self.factory.begin() as db:
            context = db.get(Context, e["search_id"])
            self.assertEqual(context.interaction_count, 1)
            context.expires_at = repo.utcnow()-timedelta(seconds=1)
        with self.assertRaises(PermissionError):
            repo.accept_interaction("owner", e["search_id"], interaction)

    def test_duplicate_and_out_of_order_projection(self):
        e = event()
        interaction = dict(e, event_id=str(uuid4()), event_type="search.feedback_submitted", feedback="not_helpful")
        for item in (interaction, e, e, interaction):
            with self.factory.begin() as db:
                repo.apply_event(db, "analytics-v1", item)
        with self.factory() as db:
            state = db.get(Projection, ("analytics-v1", e["search_id"])).state
            self.assertTrue(state["completed"])
            self.assertEqual(state["feedback"], "not_helpful")
        self.assertEqual(next(iter(repo.dashboard()["modes"].values()))["searches"], 1)

    def test_null_quarantines(self):
        self.assertEqual(repo.consume_record("test", "topic", 0, 1, None), "quarantined")
        with self.factory() as db:
            self.assertIsNotNone(db.get(Quarantine, ("test", "topic", 0, 1)))

    def test_batch_reordering_duplicates_and_quarantine(self):
        e = event()
        feedback = dict(e, event_id=str(uuid4()), event_type="search.feedback_submitted", feedback="not_helpful")
        records = [("topic", 0, i, raw) for i, raw in enumerate(
            [repo.encoded(feedback), None, repo.encoded(e), repo.encoded(e)])]
        self.assertEqual(repo.consume_batch("analytics-v1", records), ["applied", "quarantined", "applied", "duplicate"])
        self.assertEqual(repo.consume_batch("analytics-v1", records), ["duplicate", "quarantined", "duplicate", "duplicate"])
        with self.factory() as db:
            state = db.get(Projection, ("analytics-v1", e["search_id"])).state
            self.assertTrue(state["completed"])
            self.assertEqual(state["feedback"], "not_helpful")

    def test_batch_failure_rolls_back_all_effects(self):
        from app.db.search_event_models import SearchEventCheckpoint, SearchEventReceipt
        records = [("topic", 0, 0, None), ("topic", 1, 0, repo.encoded(event()))]
        with patch.object(repo, "apply_event", side_effect=RuntimeError("database failure")):
            with self.assertRaises(RuntimeError):
                repo.consume_batch("analytics-v1", records)
        with self.factory() as db:
            for model in (Projection, Quarantine, SearchEventCheckpoint, SearchEventReceipt):
                self.assertEqual(db.query(model).count(), 0)

    def test_bulk_withdrawal_hides_late_capture_and_works_with_intake_off(self):
        e = event()
        with patch.dict(os.environ, {"SEARCH_EVENTS_INTAKE_ENABLED": "false"}):
            self.assertEqual(self.client.delete("/search-sharing").status_code, 204)
        repo.capture_search("owner", "late commit", e, [], True)
        repo.consume_record("review-v1", "topic", 0, 0, repo.encoded(e))
        self.assertEqual(repo.dashboard()["review"], [])

    def test_query_revocation_and_reviewer_auth(self):
        e = event()
        repo.capture_search("owner", "shared query", e, [], True)
        with self.factory.begin() as db:
            repo.apply_event(db, "review-v1", e)
        self.assertEqual(len(repo.dashboard()["review"]), 1)
        self.assertEqual(self.client.get("/search-analytics").status_code, 403)
        self.app.dependency_overrides[get_current_user] = lambda: AuthUser("reviewer")
        self.assertEqual(self.client.get("/search-analytics").status_code, 200)
        repo.withdraw("owner", e["search_id"])
        self.assertEqual(repo.dashboard()["review"], [])

    def test_current_video_access_required(self):
        e = event()
        refs = [{"video_id": "video", "revision_id": "revision", "passage_id": "passage"}]
        repo.capture_search("owner", "shared", e, refs, True)
        with self.factory.begin() as db:
            db.add(Video(video_id="video", owner_user_id="owner", current_search_revision_id="revision"))
            repo.apply_event(db, "review-v1", e)
        self.assertEqual(len(repo.dashboard()["review"]), 1)
        with self.factory.begin() as db:
            db.get(Video, "video").owner_user_id = "other"
        self.assertEqual(repo.dashboard()["review"], [])
        repo.maintenance()
        with self.factory() as db:
            self.assertIsNone(db.get(Context, e["search_id"]).query)

    def test_expired_cleanup_beyond_first_thousand(self):
        with self.factory.begin() as db:
            for index in range(1002):
                db.add(Context(search_id=str(index), owner_id="owner", shared=0, results=[], completion={},
                    expires_at=repo.utcnow()+timedelta(days=1) if index < 1001 else repo.utcnow()-timedelta(days=1), interaction_count=0))
        repo.maintenance()
        with self.factory() as db:
            self.assertIsNone(db.get(Context, "1001"))

    def test_expired_outbox_drains_multiple_batches_and_preserves_budget(self):
        with self.factory.begin() as db:
            for i in range(1002):
                db.add(Outbox(event_id=str(i), search_id=str(i), payload={}, fingerprint="f", size=2,
                    created_at=repo.utcnow()-timedelta(days=8)))
            db.get(Budget, "outbox").pending_bytes = 2004
        repo.maintenance()
        with self.factory() as db:
            self.assertEqual(db.query(Outbox).count(), 0)
            counter = db.get(Budget, "outbox")
            self.assertEqual((counter.pending_bytes, counter.expired_events), (0, 1002))

    def test_review_pagination_and_occurrence_window(self):
        with self.factory.begin() as db:
            for i in range(52):
                e = event()
                db.add(Context(search_id=e["search_id"], owner_id="owner", query="query", shared=1,
                    results=[], completion=e, expires_at=repo.utcnow()+timedelta(days=1), interaction_count=0))
                repo.apply_event(db, "review-v1", e)
            old = event()
            old["occurred_at"] = (repo.utcnow()-timedelta(days=8)).isoformat()+"+00:00"
            repo.apply_event(db, "analytics-v1", old)
        page = repo.dashboard()
        self.assertEqual(page["modes"], {})
        self.assertEqual(len(page["review"]), 50)
        last = repo.dashboard(page["review_next_cursor"])
        self.assertEqual(len(last["review"]), 2)
        self.assertIsNone(last["review_next_cursor"])

    def test_body_bound_without_length(self):
        response = self.client.post(f"/search/{uuid4()}/interactions", content=iter([b"x"*5000, b"y"*5000]), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)

    def test_schemas_reject_forged_fields_and_rank(self):
        with self.assertRaises(ValueError):
            Interaction(event_id=str(uuid4()), event_type="search.results_viewed", ranks=[1, 1])
        e = event()
        with self.assertRaises(ValueError):
            SearchEvent.model_validate(dict(e, query="private"))
        with self.assertRaises(ValueError):
            SearchEvent.model_validate(dict(e, event_type="search.result_opened", rank=1))

    def test_capture_failures_do_not_change_search(self):
        for target in ("completion", "capture"):
            future = Future()
            future.set_result((200, {"results": [], "effective_mode": "semantic"}))
            with patch("app.api.search.submit_search", return_value=future), patch("app.services.search_events."+target, side_effect=RuntimeError("test")):
                response = self.client.post("/search", json={"query": "heap", "analytics_opt_in": True})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["interaction_enabled"])

    def test_capture_timeout_holds_slot_until_worker_finishes(self):
        release = Event()
        async def exercise():
            with patch.object(repo, "capture_search", side_effect=lambda *args: release.wait(1)):
                self.assertFalse(await capture("owner", "q", event(), [], False))
                self.assertFalse(await capture("owner", "q", event(), [], False))
                self.assertFalse(await capture("owner", "q", event(), [], False))
                release.set()
                await asyncio.sleep(.05)
        try:
            asyncio.run(exercise())
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
