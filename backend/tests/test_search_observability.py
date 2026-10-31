from concurrent.futures import Future
from contextvars import copy_context
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.search import router
from app.services import search_observability as obs
from app.services.auth_service import AuthUser, get_current_user


class SearchObservabilityTest(unittest.TestCase):
    def test_trace_parentage_and_no_request_content(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        token = obs.trace_id.set("test-trace")
        previous_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        try:
            with patch.object(obs.logger, "handlers", [handler]), patch.object(obs.logger, "disabled", False):
                with obs.span("request"):
                    def child():
                        with obs.span("lexical"):
                            pass
                    copy_context().run(child)
        finally:
            obs.trace_id.reset(token)
            logging.disable(previous_disable)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(events[0]["parent_span_id"], events[1]["span_id"])
        self.assertEqual({e["trace_id"] for e in events}, {"test-trace"})
        self.assertNotIn("query", events[0])
        self.assertNotIn("text", events[0])

    def test_histograms_include_zero_buckets_and_error_spans(self):
        with patch.object(obs, "counts", obs.defaultdict(float)):
            obs.observe("vindex_library_request_seconds", {"mode": "hybrid", "outcome": "ok"}, .2)
            with self.assertRaises(ValueError):
                with obs.span("scope"):
                    raise ValueError("must not appear in telemetry")
            rendered = obs.render_metrics()
            self.assertIn('le="0.01",mode="hybrid",outcome="ok"} 0.0', rendered)
            self.assertIn('le="+Inf",mode="hybrid",outcome="ok"} 1.0', rendered)
            self.assertIn('stage="scope",status="error"', rendered)
            self.assertNotIn("must not appear", rendered)

    def test_metrics_disabled_or_token_protected_and_errors_correlated(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: AuthUser("owner")
        with TestClient(app) as client, patch.dict(os.environ, {"SEARCH_METRICS_TOKEN": "", "LIBRARY_SEARCH_ENABLED": "true"}):
            self.assertEqual(client.get("/internal/search-metrics").status_code, 404)
            with patch.dict(os.environ, {"SEARCH_METRICS_TOKEN": "test-secret"}):
                self.assertEqual(client.get("/internal/search-metrics").status_code, 401)
                self.assertEqual(client.get("/internal/search-metrics", headers={"Authorization": "Bearer test-secret"}).status_code, 200)
            future = Future()
            future.set_exception(RuntimeError("sensitive query content"))
            with patch("app.api.search.submit_search", return_value=future):
                response = client.post("/search", json={"query": "private query"})
            self.assertEqual(response.status_code, 503)
            self.assertTrue(response.headers["x-request-id"])
            self.assertNotIn("private", obs.render_metrics())
