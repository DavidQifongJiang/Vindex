from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.services.search_contracts import Passage
from benchmarks.candidate_diagnostics import analyze, content_key, coverage_bounds, human_labels


class CandidateDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.hit = dict(Passage(str(uuid4()), str(uuid4()), 0, 0, 1000, "An answer.").as_hit(), score=1)
        self.query = {"query_id": "q1", "query": "Why?", "query_type": "conceptual"}
        self.row = dict(self.query, profile="baseline", round=0, http_status=200, degraded=False,
            coverage={"complete": True}, results=[self.hit], candidates={
                "branches": {b: {"status": "ok"} for b in ("semantic", "lexical")},
                "rankings": {b: [self.hit] for b in ("semantic", "lexical")}})
        self.raw = {"completed": True, "summary": {"integrity_complete": True},
                    "plan": {"queries": [self.query], "profiles": ["baseline"], "rounds": 1}, "rows": [self.row]}
        self.corpus = [Passage.from_hit(self.hit).as_hit()]
        self.raw["corpus_sha256"] = sha256(json.dumps(self.corpus, sort_keys=True).encode()).hexdigest()

    def analyze(self, raw=None, labels=None):
        return analyze(raw or self.raw, labels or {}, self.corpus)

    def test_unknown_is_not_irrelevant(self):
        summary, pool = self.analyze()
        bounds = summary["profiles"]["baseline"]["candidate_answer_coverage_bounds"]
        self.assertEqual((bounds["lower"], bounds["upper"]), (0, 1))
        self.assertIsNone(pool[0]["rating"])
        self.assertNotIn("rank", pool[0])
        self.assertNotIn("profile", pool[0])
        self.assertEqual(summary["release_gate"], "BLOCKED")

    def test_verified_positive_determines_coverage_without_grading_every_candidate(self):
        labels = {content_key("Why?", self.hit): {"rating": 2, "provenance": "human_verified_exact_content"}}
        summary, _ = self.analyze(labels=labels)
        self.assertEqual(summary["profiles"]["baseline"]["candidate_answer_coverage_bounds"]["lower"], 1)
        self.assertEqual(coverage_bounds([0, None, 2])["value"], 1)
        self.assertIsNone(coverage_bounds([0, None])["value"])
        self.assertEqual(coverage_bounds([])["value"], 0)

    def test_failures_are_not_dropped_from_denominator(self):
        self.row["http_status"] = 503
        summary, _ = self.analyze()
        values = summary["profiles"]["baseline"]["top5_human_only"]["hybrid"]
        self.assertEqual(values["queries"], 1)
        self.assertIsNone(values["hit_at_5"]["value"])
        self.assertEqual(len(summary["request_failures"]), 1)

    def test_inconsistent_and_duplicate_results_fail_closed(self):
        bad = deepcopy(self.raw)
        bad["rows"].append(deepcopy(self.row))
        with self.assertRaises(ValueError):
            self.analyze(bad)
        self.row["results"] = []
        with self.assertRaises(ValueError):
            self.analyze()

    def test_changed_text_and_response_timestamps_rejected(self):
        bad = deepcopy(self.raw)
        bad["rows"][0]["candidates"]["rankings"]["semantic"][0]["text"] = "tampered"
        with self.assertRaisesRegex(ValueError, "differs from frozen corpus"):
            self.analyze(bad)
        bad = deepcopy(self.raw)
        bad["rows"][0]["results"][0]["end"] = 20.0
        with self.assertRaises(ValueError):
            self.analyze(bad)

    def test_later_round_errors_are_reported(self):
        self.raw["plan"]["rounds"] = 2
        later = dict(self.row, round=1, http_status=503, candidates=None, results=[])
        self.raw["rows"].append(later)
        summary, _ = self.analyze()
        self.assertEqual(summary["request_failures"][0]["round"], 1)
        self.assertEqual(summary["profiles"]["baseline"]["top5_human_only"]["hybrid"]["queries"], 1)

    def test_labels_require_exact_query_and_content(self):
        key = content_key("Why?", self.hit)
        for query, hit in [("Why not?", self.hit), ("Why?", dict(self.hit, text="Another answer")),
                           ("Why?", dict(self.hit, video_id=str(uuid4())))]:
            self.assertNotEqual(key, content_key(query, hit))

    def test_import_ignores_ai_drafts(self):
        row = {"query": "Why?", "candidate": {"passage": self.hit}, "sample_id": "s1",
               "human_rating": 2, "status": "ai_draft"}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "labels.json"
            path.write_text(json.dumps({"items": [row]}))
            self.assertEqual(human_labels(path), {})
            row["status"] = "human_verified"
            path.write_text(json.dumps({"items": [row]}))
            self.assertEqual(len(human_labels(path)), 1)


if __name__ == "__main__":
    unittest.main()
