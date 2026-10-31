import unittest
from benchmarks.score_context_full_quality import calibration


class ContextFullQualityTests(unittest.TestCase):
    def test_false_negative_judge_cannot_pass_on_agreement_alone(self):
        reference={str(i):2 if i<2 else 0 for i in range(10)}
        rows=[dict(review_id=k,rating=0) for k in reference]
        result=calibration(rows,reference)
        self.assertEqual(result['agreement'],.8)
        self.assertFalse(result['passed'])

    def test_missing_calibration_is_rejected(self):
        with self.assertRaises(ValueError):calibration([],{'a':2})

    def test_duplicate_calibration_is_rejected(self):
        with self.assertRaises(ValueError):calibration([dict(review_id='a',rating=2)]*2,{'a':2})

    def test_unresolved_is_not_a_negative_grade(self):
        result=calibration([dict(review_id='a',rating=None)],{'a':0})
        self.assertEqual(result['unresolved'],1)
        self.assertEqual(result['agreement'],0)
        self.assertFalse(result['passed'])

    def test_exact_reference_match_passes(self):
        reference={'a':0,'b':1,'c':2}
        result=calibration([dict(review_id=k,rating=v) for k,v in reference.items()],reference)
        self.assertTrue(result['passed'])
