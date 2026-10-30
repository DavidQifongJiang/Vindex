from concurrent.futures import Future
from copy import deepcopy
import json
import os
from threading import Barrier, Event, BoundedSemaphore
from time import monotonic
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.search import router
from app.services.auth_service import AuthUser, get_current_user
from app.services import library_search as service
from app.services.search_contracts import LibrarySearchQuery, Passage, TranscriptSnapshot
from app.db.models import Base, Video, SearchProjection, SearchGeneration, SearchConfiguration, SearchRevision


class LibrarySearchTest(unittest.TestCase):
    def setUp(self):
        self.video, self.revision = str(uuid4()), str(uuid4())
        self.passage = Passage(self.video, self.revision, 0, 0, 1000, "Dijkstra Fibonacci heap")
        self.attempts = {b: str(uuid4()) for b in ("semantic", "lexical")}
        self.scope = service.Scope({self.video: {"title": "Algorithms"}},
            {b: {self.video: (self.revision, a)} for b, a in self.attempts.items()},
            {b: {"id": b, "target": b, "settings": {}} for b in self.attempts}, set())

    def retrieve(self, backend, *args):
        return [dict(self.passage.as_hit(), projection_id=self.attempts[backend], score=4)]

    def search(self, retriever=None, current=None, mode="hybrid"):
        scopes = iter([self.scope, current or self.scope])
        return service.search_library("owner", LibrarySearchQuery("heap", mode=mode), monotonic()+3,
            scope_reader=lambda *_: next(scopes), retriever=retriever or self.retrieve)

    def test_parallel_hybrid_deduplicates_canonical_passage(self):
        barrier = Barrier(2)
        def retrieve(*args):
            barrier.wait(timeout=1)
            return self.retrieve(*args)
        code, result = self.search(retrieve)
        self.assertEqual(code, 200)
        self.assertEqual(result["effective_mode"], "hybrid")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["title"], "Algorithms")
        self.assertEqual(result["results"][0]["matched_by"], ["semantic", "lexical"])
        self.assertEqual(result["results"][0]["start_seconds"], 0)
        self.assertNotIn("score", result["results"][0])

    def test_one_branch_failure_returns_survivor(self):
        def retrieve(backend, *args):
            if backend == "semantic":
                raise ConnectionError("private internal details")
            return self.retrieve(backend, *args)
        code, result = self.search(retrieve)
        self.assertEqual(code, 200)
        self.assertTrue(result["degraded"])
        self.assertEqual(result["notices"], ["semantic_unavailable"])

    def test_transport_timeouts_are_reported_as_timeouts(self):
        import requests
        import httpx
        from qdrant_client.http.exceptions import ResponseHandlingException
        for error in (requests.Timeout(), ResponseHandlingException(httpx.ReadTimeout("timeout"))):
            def retrieve(backend, *args):
                if backend == "semantic":
                    raise error
                return self.retrieve(backend, *args)
            self.assertEqual(self.search(retrieve)[1]["branches"]["semantic"]["status"], "timeout")

    def test_both_failed_and_successful_empty_are_distinct(self):
        def failed(*_):
            raise ConnectionError()
        self.assertEqual(self.search(failed)[0], 503)
        code, result = self.search(lambda *_: [])
        self.assertEqual(code, 200)
        self.assertFalse(result["degraded"])
        self.assertEqual(result["results"], [])

    def test_pending_and_terminal_failure_notices(self):
        self.scope.projections = {"semantic": {}, "lexical": {}}
        self.assertEqual(self.search()[0], 409)
        self.scope.failed.add(("lexical", self.video))
        code, result = self.search()
        self.assertEqual(code, 409)
        self.assertIn("indexing_failed", result["notices"])
        self.assertNotIn("video_processing", result["notices"])

    def test_ownership_revoked_before_return(self):
        current = service.Scope({}, {"semantic": {}, "lexical": {}}, self.scope.generations, set())
        code, result = self.search(current=current)
        self.assertEqual(code, 200)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["coverage"]["total_videos"], 0)

    def test_repaired_attempt_and_generation_switch_exclude_old_hits(self):
        for change in ("attempt", "generation"):
            current = deepcopy(self.scope)
            if change == "attempt":
                current.projections = {b: {self.video: (self.revision, str(uuid4()))} for b in self.attempts}
            else:
                current.generations = {}
            code, result = self.search(current=current)
            self.assertEqual(code, 409)
            self.assertEqual(result["results"], [])

    def test_foreign_hit_is_discarded_even_if_engine_filter_fails(self):
        foreign = Passage(str(uuid4()), self.revision, 0, 0, 1000, "private")
        code, result = self.search(lambda *_: [dict(foreign.as_hit(), projection_id=self.attempts["lexical"], score=10)])
        self.assertEqual(code, 200)
        self.assertEqual(result["results"], [])

    def test_final_authorization_failure_fails_closed(self):
        calls = iter([self.scope])
        with self.assertRaises(StopIteration):
            service.search_library("owner", LibrarySearchQuery("heap"), monotonic()+3,
                scope_reader=lambda *_: next(calls), retriever=self.retrieve)

    def test_candidate_observer_only_sees_revalidated_bounded_copies(self):
        captured = []
        def observer(snapshot):
            captured.append(deepcopy(snapshot))
            snapshot["rankings"]["semantic"].clear()
        def many(backend, *args):
            return [dict(Passage(self.video, self.revision, i, i * 1000, (i+1)*1000,
                                 f"passage {i}").as_hit(), projection_id=self.attempts[backend], score=100-i)
                    for i in range(60)]
        code, payload = service.search_library("owner", LibrarySearchQuery("heap"), monotonic()+3,
            scope_reader=lambda *_: self.scope, retriever=many, candidate_observer=observer)
        self.assertEqual(code, 200)
        self.assertEqual(len(payload["results"]), 5)
        self.assertEqual(len(captured[0]["rankings"]["semantic"]), 50)
        self.assertNotIn("rankings", payload)
        self.assertTrue(all("projection_id" not in h for h in captured[0]["rankings"]["semantic"]))

    def test_candidate_observer_cannot_export_deleted_or_foreign_text(self):
        captured = []
        current = service.Scope({}, {"semantic": {}, "lexical": {}}, {}, set())
        scopes = iter([self.scope, current])
        service.search_library("owner", LibrarySearchQuery("heap"), monotonic()+3,
            scope_reader=lambda *_: next(scopes), retriever=self.retrieve, candidate_observer=captured.append)
        self.assertTrue(all(not hits for hits in captured[0]["rankings"].values()))
        foreign = Passage(str(uuid4()), self.revision, 0, 0, 1000, "private")
        service.search_library("owner", LibrarySearchQuery("heap"), monotonic()+3,
            scope_reader=lambda *_: self.scope,
            retriever=lambda backend, *_: [dict(foreign.as_hit(), projection_id=self.attempts[backend], score=1)],
            candidate_observer=captured.append)
        self.assertTrue(all(not hits for hits in captured[1]["rankings"].values()))

    def test_single_mode_never_executes_other_branch(self):
        def retrieve(backend, *args):
            self.assertEqual(backend, "lexical")
            return self.retrieve(backend, *args)
        self.assertEqual(self.search(retrieve, mode="lexical")[1]["branches"]["semantic"]["status"], "not_requested")

    def test_timeout_delivers_survivor_while_retaining_admission(self):
        release, entered = Event(), Event()
        gate = BoundedSemaphore(1)
        def retrieve(backend, *args):
            if backend == "semantic":
                entered.set()
                release.wait(5)
            return self.retrieve(backend, *args)
        original = service.search_library
        def execute(owner, query, deadline, **kwargs):
            return original(owner, query, deadline, scope_reader=lambda *_: self.scope, retriever=retrieve, **kwargs)
        with patch.object(service, "ADMISSION", gate), patch.object(service, "search_library", side_effect=execute):
            try:
                future = service.submit_search("owner", LibrarySearchQuery("heap", mode="hybrid"), monotonic()+.6)
                self.assertTrue(entered.wait(1))
                code, result = future.result(timeout=1)
                self.assertEqual(code, 200)
                self.assertEqual(result["branches"]["semantic"]["status"], "timeout")
                with self.assertRaises(service.SearchError) as error:
                    service.submit_search("owner", LibrarySearchQuery("heap"), monotonic()+3)
                self.assertEqual(error.exception.status, 429)
            finally:
                release.set()
                self.assertTrue(gate.acquire(timeout=2))
                gate.release()


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)

    def read(self):
        return service.read_scope("owner", monotonic()+1, self.sessions)

    def test_scope_excludes_other_users_public_videos_and_deleted(self):
        with self.sessions() as db:
            db.add_all([Video(video_id="mine", owner_user_id="owner", status="processed"),
                Video(video_id="public", owner_user_id="other", visibility="public", status="processed"),
                Video(video_id="deleting", owner_user_id="owner", status="deleting")])
            db.commit()
        self.assertEqual(set(self.read().videos), {"mine"})

    def test_scope_cap_rejects_instead_of_truncating(self):
        with self.sessions() as db:
            db.add_all([Video(video_id=str(i), owner_user_id="owner", status="processed") for i in range(101)])
            db.commit()
        with self.assertRaises(service.SearchError) as error:
            self.read()
        self.assertEqual(error.exception.code, "library_scope_too_large")

    def published_video(self):
        from app.db import search_repository as repo
        video, attempt = str(uuid4()), str(uuid4())
        raw = b'[{"start":0,"end":1,"text":"heap"}]'
        with self.sessions() as db, patch.dict(os.environ, {"SEARCH_EMBEDDING_REVISION": ""}):
            db.add(Video(video_id=video, owner_user_id="owner", status="processed", title="Algorithms",
                         segments_status="completed", segments_path="accepted.json"))
            db.commit()
            revision = repo.register_revision(db, video, "accepted.json", raw)
            revision_id = revision.revision_id
            job = db.query(SearchProjection).filter_by(video_id=video).one()
            job.status, job.accepted_attempt_id = "ready", attempt
            db.commit()
        return video, revision_id, attempt

    def test_only_ready_current_revision_is_eligible(self):
        video, revision, attempt = self.published_video()
        self.assertEqual(self.read().projections["lexical"], {video: (revision, attempt)})
        with self.sessions() as db:
            db.get(Video, video).segments_path = "new.json"
            db.commit()
        self.assertEqual(self.read().projections["lexical"], {})


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Requires isolated PostgreSQL")
class PostgresScopeTest(ScopeTest):
    def setUp(self):
        self.schema = "vindex_library_" + uuid4().hex
        self.root = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"])
        with self.root.begin() as conn:
            conn.execute(CreateSchema(self.schema))
        self.engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"],
            connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)

    def cleanup(self):
        self.engine.dispose()
        with self.root.begin() as conn:
            conn.execute(DropSchema(self.schema, cascade=True))
        self.root.dispose()

    def test_ownership_change_during_real_request_removes_text(self):
        video, revision, attempt = self.published_video()
        def retrieve(*_):
            with self.sessions() as db:
                db.get(Video, video).owner_user_id = "other"
                db.commit()
            return [dict(Passage(video, revision, 0, 0, 1000, "heap").as_hit(), projection_id=attempt, score=1)]
        code, result = service.search_library("owner", LibrarySearchQuery("heap", mode="lexical"), monotonic()+3,
            scope_reader=lambda owner, deadline: service.read_scope(owner, deadline, self.sessions), retriever=retrieve)
        self.assertEqual(code, 200)
        self.assertEqual(result["results"], [])
        self.assertIn("search_scope_changed", result["notices"])


class LibraryAPITest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.app, self.client = app, TestClient(app)
        app.dependency_overrides[get_current_user] = lambda: AuthUser("owner")
        enabled = patch.dict(os.environ, {"LIBRARY_SEARCH_ENABLED": "true"})
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_input_validation(self):
        for body in ({"query": " "}, {"query": "x", "limit": True}, {"query": "x", "owner": "other"}, {"query": "x", "scope": "public"}):
            self.assertEqual(self.client.post("/search", json=body).status_code, 422)

    def test_disabled_and_unauthenticated(self):
        with patch.dict(os.environ, {"LIBRARY_SEARCH_ENABLED": "false"}):
            self.assertEqual(self.client.post("/search", json={"query": "x"}).status_code, 404)
        self.app.dependency_overrides.clear()
        with patch.dict(os.environ, {"AUTH_REQUIRED": "true"}):
            self.assertEqual(self.client.post("/search", json={"query": "x"}).status_code, 401)

    def test_overload_and_scope_failure_codes(self):
        with patch("app.api.search.submit_search", side_effect=service.SearchError("library_search_overloaded", 429)):
            response = self.client.post("/search", json={"query": "x"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "1")
        future = Future()
        future.set_exception(RuntimeError("database secret"))
        with patch("app.api.search.submit_search", return_value=future):
            response = self.client.post("/search", json={"query": "x"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret", response.text)

    def test_response_includes_request_id_and_uses_authenticated_owner(self):
        future = Future()
        future.set_result((200, {"effective_mode": "none", "results": []}))
        with patch("app.api.search.submit_search", return_value=future) as submit:
            response = self.client.post("/search", json={"query": "heap"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], response.json()["request_id"])
        self.assertEqual(submit.call_args.args[0], "owner")


class BackendFilterTest(unittest.TestCase):
    def test_semantic_adapter_encodes_once_and_checks_model_provenance(self):
        model, client = MagicMock(), MagicMock()
        model.__getitem__.return_value.auto_model.config._commit_hash = "pinned-commit"
        model.encode.return_value.tolist.return_value = [1., 0.]
        client.query_points.return_value = SimpleNamespace(points=[])
        generation = {"target": "library_test", "settings": {
            "model": "test-model", "model_revision": "pinned-commit", "dimension": 2}}
        scope = {str(uuid4()): (str(uuid4()), str(uuid4()))}
        with patch.dict(os.environ, {"EMBEDDING_MODEL": "test-model"}), \
             patch("app.services.search_service.get_embedding_model", return_value=model), \
             patch("app.services.search_clients.QdrantClient", return_value=client) as constructor:
            from app.services.search_clients import close_search_clients
            close_search_clients()
            self.addCleanup(close_search_clients)
            self.assertEqual(service.retrieve("semantic", generation, scope, "heap", monotonic()+2), [])
            model.encode.assert_called_once_with("heap")
            self.assertLessEqual(constructor.call_args.kwargs["timeout"], 2)
            self.assertEqual(client.query_points.call_args.kwargs["query_filter"], service.semantic_filter(scope))
            client.close.assert_not_called()
            model.__getitem__.return_value.auto_model.config._commit_hash = "wrong-model"
            with self.assertRaises(ValueError):
                service.retrieve("semantic", generation, scope, "heap", monotonic()+2)
            self.assertEqual(constructor.call_count, 1)

    def test_empty_filters_cannot_expand_to_all_documents(self):
        for build in (service.lexical_filter, service.semantic_filter):
            with self.assertRaises(ValueError):
                build({})

    def test_qdrant_tuple_filter_excludes_wrong_revision_attempt_and_video(self):
        from qdrant_client import QdrantClient
        from app.services.projection_indexes import QdrantProjectionIndex
        client = QdrantClient(":memory:")
        self.addCleanup(client.close)
        index = QdrantProjectionIndex("library_test", 2, client)
        snapshot = TranscriptSnapshot.from_manifest(str(uuid4()), str(uuid4()), b'[{"start":0,"end":1,"text":"heap"}]')
        accepted, obsolete = str(uuid4()), str(uuid4())
        index.write(snapshot, accepted, [[1., 0.]])
        index.write(snapshot, obsolete, [[1., 0.]])
        scope = {snapshot.video_id: (snapshot.revision_id, accepted)}
        result = client.query_points(index.target, query=[1., 0.], query_filter=service.semantic_filter(scope)).points
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].payload["projection_id"], accepted)
        wrong = {snapshot.video_id: (str(uuid4()), accepted)}
        self.assertEqual(client.query_points(index.target, query=[1., 0.], query_filter=service.semantic_filter(wrong)).points, [])

    @unittest.skipUnless(os.getenv("VINDEX_TEST_ELASTICSEARCH_URL"), "Requires real Elasticsearch")
    def test_real_lexical_retrieval_uses_only_published_tuple(self):
        from app.services.projection_indexes import ElasticsearchProjectionIndex
        target = "vindex-phase3-test-" + uuid4().hex
        client = ElasticsearchProjectionIndex(target, os.environ["VINDEX_TEST_ELASTICSEARCH_URL"])
        snapshot = TranscriptSnapshot.from_manifest(str(uuid4()), str(uuid4()), b'[{"start":0,"end":1,"text":"Fibonacci heap"}]')
        accepted, obsolete = str(uuid4()), str(uuid4())
        try:
            client.write(snapshot, accepted)
            client.write(snapshot, obsolete)
            with patch.dict(os.environ, {"ELASTICSEARCH_URL": os.environ["VINDEX_TEST_ELASTICSEARCH_URL"]}):
                hits = service.retrieve("lexical", {"target": target},
                    {snapshot.video_id: (snapshot.revision_id, accepted)}, "heap", monotonic()+2)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["projection_id"], accepted)
        finally:
            client.request("DELETE", target)
            client.close()

