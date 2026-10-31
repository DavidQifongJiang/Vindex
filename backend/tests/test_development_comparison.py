import unittest
from benchmarks.evaluate_development_comparison import metrics
from benchmarks.replay_development_fusion import fuse


class ComparisonMetricsTest(unittest.TestCase):
    def test_partial_answer_is_not_strict_hit(self):
        result = metrics([1,0,2,0,0], [2,1,0,0,0])
        self.assertEqual(result['answer_hit1'], 0)
        self.assertEqual(result['answer_hit5'], 1)
        self.assertAlmostEqual(result['answer_mrr5'], 1/3)
        self.assertEqual(result['answer_precision5'], .2)
        self.assertLess(result['pooled_ndcg5'], 1)

    def test_unjudged_is_not_irrelevant(self):
        with self.assertRaises(ValueError):
            metrics([None], [2])

    def test_empty_and_all_irrelevant(self):
        self.assertEqual(metrics([], [0])['pooled_ndcg5'], 0)
        self.assertEqual(metrics([0]*5, [0]*5)['answer_hit5'], 0)

    def test_ideal_ranking(self):
        self.assertEqual(metrics([2,2,1,0,0], [0,2,1,2,0])['pooled_ndcg5'], 1)

    def test_weighted_fusion_and_ties(self):
        dense = [{'passage_id':'a'}, {'passage_id':'b'}]
        sparse = [{'passage_id':'b'}, {'passage_id':'a'}]
        self.assertEqual(fuse(dense, sparse, .5)[0]['passage_id'], 'a')
        self.assertEqual(fuse(dense, sparse, .75)[0]['passage_id'], 'a')
        self.assertEqual(fuse(dense, sparse, .25)[0]['passage_id'], 'b')

    def test_depth5_weight75_cannot_add_lexical_only_hits(self):
        dense = [{'passage_id':f'd{i}'} for i in range(5)]
        sparse = [{'passage_id':f'l{i}'} for i in range(5)]
        self.assertEqual({x['passage_id'] for x in fuse(dense,sparse,.75)}, {x['passage_id'] for x in dense})
