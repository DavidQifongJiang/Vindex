import tempfile
import unittest
from pathlib import Path
from benchmarks.score_context_quality_pilot import read_assessments


class ContextQualityPilotTests(unittest.TestCase):
    def assess(self, text):
        pool=[dict(item=1,review_id='one',query='why',text='The reason is scaling.')]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'notes.txt'
            path.write_text(text,encoding='utf-8')
            return read_assessments(pool,path)

    def test_cited_judgment_preserves_ai_provenance(self):
        result=self.assess('1|2|reason is scaling|Explains the reason.')
        self.assertEqual(result['one']['rating'],2)
        self.assertEqual(result['one']['provenance'],'ai_review_draft')

    def test_invented_evidence_is_rejected(self):
        with self.assertRaises(ValueError):self.assess('1|2|not in passage|Explanation')

    def test_missing_assessment_is_not_zero(self):
        with self.assertRaises(ValueError):self.assess('')

    def test_duplicate_assessment_is_rejected(self):
        with self.assertRaises(ValueError):self.assess('1|0||Wrong task\n1|2|scaling|Correct')

    def test_out_of_range_grade_is_rejected(self):
        with self.assertRaises(ValueError):self.assess('1|3|scaling|Invalid')
