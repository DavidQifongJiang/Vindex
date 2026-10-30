import unittest
from unittest.mock import patch

from qdrant_client import QdrantClient
from app.services.qdrant_service import search_segments, upsert_segments


class FencedVectorsTest(unittest.TestCase):
    def test_only_committed_attempts_are_searchable_even_after_late_upserts(self):
        client = QdrantClient(":memory:")
        self.addCleanup(client.close)
        vector = [1.0] + [0.0] * 383

        def segment(index, text, chunk=0):
            return {"segment_id": f"{chunk}:{index}", "chunk_index": chunk,
                    "start": float(index), "end": float(index + 1), "text": text, "embedding": vector}

        with patch("app.services.qdrant_service.get_qdrant_client", return_value=client):
            upsert_segments("v1", [segment(0, "old"), segment(1, "extra old")], attempt_token="old-attempt")
            upsert_segments("v1", [segment(0, "accepted")], attempt_token="winner")
            upsert_segments("v1", [segment(0, "late overwrite")], attempt_token="old-attempt")
            upsert_segments("other-video", [segment(0, "other")], attempt_token="winner")
            results = search_segments(vector, "v1", chunk_attempts=[(0, "winner")])
            self.assertEqual([result["text"] for result in results], ["accepted"])
            upsert_segments("v1", [segment(0, "legacy completed chunk", chunk=1)])
            results = search_segments(vector, "v1", chunk_attempts=[(0, "winner"), (1, None)])
            self.assertEqual({result["text"] for result in results}, {"accepted", "legacy completed chunk"})
