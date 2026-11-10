import unittest
import json
import tempfile
from pathlib import Path

from benchmarks.analyze_upload_comparison import analyze_cell, mean_ratio_interval, paired_bootstrap, wilson


class UploadComparisonAnalysisTests(unittest.TestCase):
    def test_preserves_paired_effect_despite_large_between_wave_variation(self):
        a = [[1, 2], [100, 200], [10, 20], [50, 100]]
        b = [[v * .5 for v in row] for row in a]
        report = paired_bootstrap(a, b, iterations=500)
        self.assertEqual(report['p95']['direct_over_api'], .5)
        self.assertEqual(report['p95']['ratio_95pct_paired_wave_bootstrap'], [.5, .5])
        self.assertEqual(report['p50']['ratio_95pct_paired_wave_bootstrap'], [.5, .5])

    def test_refuses_unmatched_or_nonfinite_client_matrices(self):
        for a, b in [([[1]], [[1, 2]]), ([[float('nan')]], [[1]]), ([], [])]:
            with self.subTest(a=a), self.assertRaises(ValueError):
                paired_bootstrap(a, b)

    def test_correlated_clients_keep_wave_level_uncertainty(self):
        # Twenty clients share each wave's effect. Flattened client resampling
        # would put the p95 interval at [8, 8] and falsely remove uncertainty.
        api = [[1] * 20 for _ in range(4)]
        direct = [[effect] * 20 for effect in [1, 2, 4, 8]]
        interval = paired_bootstrap(api, direct)['p95']['ratio_95pct_paired_wave_bootstrap']
        self.assertEqual(interval, [2, 8])

    def test_incomplete_cell_cannot_be_reported_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'run.json').write_text(json.dumps({'files': [{'name': 'test.mp4'}]}))
            (root / 'completion.json').write_text(json.dumps({
                'finished_all_planned_waves': False, 'planned_attempts': 200}))
            for name in ['attempts', 'resources', 'verification']:
                (root / (name + '.jsonl')).write_text('')
            with self.assertRaisesRegex(ValueError, 'Incomplete cell'):
                analyze_cell(root)

    def test_resource_zero_and_exact_ratio(self):
        self.assertEqual(mean_ratio_interval([1, 10, 100], [0, 0, 0], iterations=100)['ratio_95pct_paired_wave_bootstrap'], [0, 0])
        self.assertEqual(mean_ratio_interval([1, 10, 100], [2, 20, 200], iterations=100)['ratio_95pct_paired_wave_bootstrap'], [2, 2])

    def test_hundred_successes_do_not_establish_99_percent_reliability(self):
        self.assertLess(wilson(100, 100)[0], .99)
        self.assertEqual(wilson(0, 0), None)


if __name__ == '__main__':
    unittest.main()
