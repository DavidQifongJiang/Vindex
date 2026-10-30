import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from app import Handler, chunk_count


class ChunkTests(unittest.TestCase):
    def test_partial_first_chunk(self):
        self.assertEqual(chunk_count(1), 1)

    def test_exact_boundary(self):
        self.assertEqual(chunk_count(300), 1)

    def test_partial_last_chunk(self):
        self.assertEqual(chunk_count(301), 2)

    def test_long_video(self):
        self.assertEqual(chunk_count(1158), 4)

    def test_invalid_duration(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                chunk_count(value)

    def test_invalid_chunk_size(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                chunk_count(300, value)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path):
        try:
            response = urlopen(self.base + path, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_plan_response(self):
        status, body = self.get("/plan?seconds=301")
        self.assertEqual(status, 200)
        self.assertEqual(body["chunks"], 2)

    def test_invalid_request(self):
        self.assertEqual(self.get("/plan?seconds=-1")[0], 400)

    def test_ready_release_identity(self):
        with patch.dict(os.environ, {"RELEASE_SHA": "example-sha", "DEMO_FAIL_READY": "0"}):
            status, body = self.get("/ready")
        self.assertEqual(status, 200)
        self.assertEqual(body["release"], "example-sha")

    def test_alive_can_be_not_ready(self):
        with patch.dict(os.environ, {"DEMO_FAIL_READY": "1"}):
            self.assertEqual(self.get("/health")[0], 200)
            self.assertEqual(self.get("/ready")[0], 503)


if __name__ == "__main__":
    unittest.main()
