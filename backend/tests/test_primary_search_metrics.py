import unittest
from benchmarks.score_search_metrics import primary_bounds, aggregate, abstention_metrics


class PrimaryMetricsTest(unittest.TestCase):
    def test_partial_does_not_count_as_answer(self):
        m = primary_bounds([1,0,2,0,0])
        self.assertEqual(m['hit_at_1']['value'],0)
        self.assertEqual(m['hit_at_5']['value'],1)
        self.assertEqual(m['mrr_at_5']['value'],1/3)

    def test_unknown_before_answer_changes_mrr_not_hit5(self):
        m = primary_bounds([None,2])
        self.assertEqual(m['hit_at_5']['value'],1)
        self.assertIsNone(m['hit_at_1']['value'])
        self.assertIsNone(m['mrr_at_5']['value'])
        self.assertEqual(m['mrr_at_5']['lower'],.5)
        self.assertEqual(m['mrr_at_5']['upper'],1)

    def test_unknown_after_first_answer_cannot_change_mrr(self):
        self.assertEqual(primary_bounds([2,None])['mrr_at_5']['value'],1)

    def test_empty_success_is_miss(self):
        self.assertEqual(primary_bounds([])['hit_at_5']['value'],0)

    def test_unknown_is_not_zero(self):
        self.assertIsNone(primary_bounds([0,None,0])['hit_at_5']['value'])

    def test_invalid_grades_rejected(self):
        for grades in [[True],[3],['2'],[0]*6]:
            with self.assertRaises(ValueError):
                primary_bounds(grades)

    def test_macro_estimate_unavailable_if_any_query_undetermined(self):
        rows=[{'bounds':primary_bounds([2])},{'bounds':primary_bounds([None])}]
        out=aggregate(rows)
        self.assertIsNone(out['hit_at_5']['value'])
        self.assertEqual(out['hit_at_5']['lower_bound'],.5)
        self.assertEqual(out['hit_at_5']['upper_bound'],1)

    def test_empty_group_is_not_zero_accuracy(self):
        self.assertIsNone(aggregate([])['hit_at_1']['value'])

    def test_no_answer_requires_verified_labels(self):
        rows=[{'query_id':'a','results':[]}]
        self.assertIsNone(abstention_metrics(rows,{})['correct_abstention_rate']['value'])
        self.assertEqual(abstention_metrics(rows,{'a':'unanswerable'})['correct_abstention_rate']['value'],1)
        self.assertEqual(abstention_metrics(rows,{'a':'answerable'})['incorrect_abstention_rate']['value'],1)

    def test_error_is_not_abstention(self):
        out=abstention_metrics([{'query_id':'a','results':[],'error':'timeout'}],{'a':'unanswerable'})
        self.assertEqual(out['request_errors'],1)
        self.assertIsNone(out['correct_abstention_rate']['value'])
