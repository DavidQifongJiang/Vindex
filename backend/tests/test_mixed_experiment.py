import importlib.util
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[1]/'benchmarks/analyze_mixed_experiment.py'
spec = importlib.util.spec_from_file_location('mixed_analysis', SOURCE)
analysis = importlib.util.module_from_spec(spec); spec.loader.exec_module(analysis)

STATE_SOURCE = SOURCE.parent/'pipeline_experiment/mixed_state.py'
state_spec = importlib.util.spec_from_file_location('mixed_state', STATE_SOURCE)
state = importlib.util.module_from_spec(state_spec)
sys.path.insert(0, str(STATE_SOURCE.parent))
try:
    state_spec.loader.exec_module(state)
finally:
    sys.path.pop(0)

PROCESSING_SOURCE = SOURCE.with_name('summarize_mixed_processing.py')
processing_spec = importlib.util.spec_from_file_location('mixed_processing', PROCESSING_SOURCE)
processing = importlib.util.module_from_spec(processing_spec)
sys.path.insert(0, str(SOURCE.parent))
try:
    processing_spec.loader.exec_module(processing)
finally:
    sys.path.pop(0)

SUMMARY_SOURCE = SOURCE.with_name('summarize_mixed_comparison.py')
summary_spec = importlib.util.spec_from_file_location('mixed_summary', SUMMARY_SOURCE)
summary = importlib.util.module_from_spec(summary_spec)
sys.path.insert(0, str(SOURCE.parent))
try:
    summary_spec.loader.exec_module(summary)
finally:
    sys.path.pop(0)


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

    def test_retirement_refuses_protected_corpus(self):
        protected = '00000000-0000-0000-0000-000000000001'
        with self.assertRaises(AssertionError): state.validate_ids([protected], [protected])

    def test_retirement_requires_a_protected_corpus_and_bounded_ids(self):
        target = '00000000-0000-0000-0000-000000000002'
        protected = '00000000-0000-0000-0000-000000000001'
        with self.assertRaises(AssertionError): state.validate_ids([target], [])
        with self.assertRaises(AssertionError): state.validate_ids([target, target], [protected])
        with self.assertRaises(ValueError): state.validate_ids(['not-a-video-id'], [protected])

    def test_bootstrap_preserves_whole_trial_pairing(self):
        result = summary.paired_ratio([1, 10, 100], [2, 20, 200], draws=1000)
        self.assertEqual(result['right_over_left'], 2)
        self.assertEqual(result['paired_95pct_interval'], [2, 2])
        self.assertEqual(result['independent_pairs'], 3)

    def test_missing_comparator_trial_is_not_silently_discarded(self):
        with self.assertRaises(AssertionError): summary.paired_ratio([1, 2, 3], [1, 2])

    def test_processing_rate_excludes_late_verification_and_uses_observed_time(self):
        samples = [
            {'epoch': 105, 'pipeline': {'searchable': 0, 'playable': 0, 'pending_videos': 1}},
            {'epoch': 155, 'pipeline': {'searchable': 2, 'playable': 3, 'pending_videos': 5}},
            {'epoch': 180, 'pipeline': {'searchable': 9, 'playable': 9, 'pending_videos': 0}}]
        result = processing.pipeline_window(samples, 100, 160)
        self.assertEqual(result['observed_seconds'], 50)
        self.assertEqual(result['searchable_completions_per_minute'], 2.4)
        self.assertEqual(result['pending_growth'], 4)

    def test_processing_rate_requires_two_in_window_observations(self):
        with self.assertRaises(AssertionError): processing.pipeline_window([], 100, 160)


if __name__ == '__main__': unittest.main()
