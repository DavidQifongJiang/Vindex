import unittest
from uuid import uuid4

from app.services.search_contracts import Passage
from app.services.search_ranking import balanced_candidates, rerank_candidates


class SearchRankingTest(unittest.TestCase):
    def setUp(self):
        video, revision = str(uuid4()), str(uuid4())
        self.hits = [dict(Passage(video, revision, i, i*1000, (i+1)*1000,
                                f"passage {i}").as_hit(), score=10-i) for i in range(8)]

    def test_small_depth_keeps_both_engines_and_deduplicates(self):
        candidates = balanced_candidates(self.hits[:4], [self.hits[0], *self.hits[4:]], depth=4)
        self.assertEqual([h["ordinal"] for h in candidates], [0, 1, 4, 2])
        self.assertEqual(len(balanced_candidates(self.hits, self.hits, depth=100)), 8)

    def test_reranker_recovers_a_candidate_beyond_final_five(self):
        result = rerank_candidates("question", self.hits, [],
            lambda pairs: [100 if text == "passage 7" else 0 for _, text in pairs], depth=8)
        self.assertEqual(result[0]["ordinal"], 7)
        self.assertEqual(self.hits[7]["score"], 3)

    def test_ties_are_deterministic(self):
        result = rerank_candidates("q", self.hits, [], lambda pairs: [0]*len(pairs))
        self.assertEqual([h["passage_id"] for h in result], sorted(h["passage_id"] for h in self.hits)[:5])

    def test_invalid_outputs_and_inputs_fail_closed(self):
        for scores in ([float("nan")]*8, [float("inf")]*8, ["1"]*8, [True]*8, [1]):
            with self.assertRaises(ValueError):
                rerank_candidates("q", self.hits, [], lambda _: scores)
        for depth in (0, 101, True):
            with self.assertRaises(ValueError):
                balanced_candidates(self.hits, [], depth)
        with self.assertRaises(ValueError):
            balanced_candidates(self.hits, [dict(self.hits[0], text="conflicting payload")])

    def test_empty_candidates_never_load_model(self):
        def fail(_):
            self.fail("Should not run inference")
        self.assertEqual(rerank_candidates("q", [], [], fail), [])


if __name__ == "__main__":
    unittest.main()
