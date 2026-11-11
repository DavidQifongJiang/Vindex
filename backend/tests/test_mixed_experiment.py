import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'benchmarks/analyze_mixed_experiment.py'
spec = importlib.util.spec_from_file_location('mixed_analysis', SOURCE)
analysis = importlib.util.module_from_spec(spec); spec.loader.exec_module(analysis)


class MixedExperimentTests(unittest.TestCase):
    def test_missing_arrivals_remain_failures(self):
        row = analysis.ledger([{'index': 0, 'outcome': 'accepted'}], 3, 'accepted')
        self.assertFalse(row['complete_unique_ledger'])
        self.assertEqual(row['success_fraction'], 1/3)

    def test_duplicate_ids_do_not_make_a_complete_ledger(self):
        row = analysis.ledger([{'index': 0, 'outcome': 'accepted'}]*2, 2, 'accepted')
        self.assertFalse(row['complete_unique_ledger'])

    def test_pool_exhaustion_counts_against_upload_success(self):
        row = analysis.ledger([{'index': 0, 'outcome': 'accepted'}, {'index': 1, 'outcome': 'not_started'}], 2, 'accepted')
        self.assertTrue(row['complete_unique_ledger'])
        self.assertEqual(row['success_fraction'], .5)

    def test_tail_keeps_deadline_failures(self):
        self.assertEqual(analysis.quantile([.1]*94+[5]*6, .95), 5)


if __name__ == '__main__': unittest.main()
