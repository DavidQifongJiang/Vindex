import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]/'benchmarks/pipeline_experiment'
sys.path.insert(0, str(SOURCE))
try:
    spec = importlib.util.spec_from_file_location('architecture_control', SOURCE/'architecture_control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
finally:
    sys.path.pop(0)

sys.path.insert(0, str(SOURCE.parent))
try:
    spec = importlib.util.spec_from_file_location('architecture_analysis', SOURCE.parent/'analyze_architecture_experiment.py')
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
finally:
    sys.path.pop(0)


class ArchitectureExperimentTests(unittest.TestCase):
    def test_control_fails_closed_outside_isolated_environment(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            control.architecture()

    def test_unknown_architecture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'architecture.json'
            path.write_text(json.dumps({'architecture': 'anything'}))
            with patch.object(control, 'validate_environment'), patch.object(control, 'CONTROL', path):
                with self.assertRaises(ValueError):
                    control.architecture()

    def test_adapter_changes_only_enqueue_flag_and_preserves_metadata(self):
        from app.db import upload_repository
        original = upload_repository.create_video
        calls = []
        def fake(db, metadata, *, enqueue_processing=False):
            calls.append((db, metadata, enqueue_processing))
            return 'committed'
        try:
            upload_repository.create_video = fake
            with patch.object(control, 'validate_environment'):
                control.install_upload_adapter()
            marker, metadata = object(), {'video_id': 'same-input'}
            for architecture, expected in [('serialized', False), ('branches', True), ('dag', True)]:
                with patch.object(control, 'architecture', return_value=architecture):
                    self.assertEqual(upload_repository.create_video(marker, metadata, enqueue_processing=True), 'committed')
                self.assertEqual(calls[-1], (marker, metadata, expected))
            with patch.object(control, 'architecture', return_value='dag'):
                upload_repository.create_video(marker, metadata, enqueue_processing=False)
            self.assertFalse(calls[-1][2])
        finally:
            upload_repository.create_video = original

    def test_branch_control_uses_one_whole_video_unit_without_changing_dag_code(self):
        with patch.dict(os.environ, {}, clear=True):
            for name, expected in [('branches', 1800), ('dag', 300), ('serialized', 300)]:
                with patch.object(control, 'architecture', return_value=name):
                    result = control.configure_worker()
                self.assertEqual(result['chunk_seconds'], expected)
                self.assertEqual(os.environ['SEARCH_CHUNK_SECONDS'], str(expected))

    def test_cumulative_reduction_is_not_sum_of_step_reductions(self):
        self.assertEqual(analysis.reduction(100, 80), 20)
        self.assertEqual(analysis.reduction(80, 40), 50)
        self.assertEqual(analysis.reduction(100, 40), 60)
        with self.assertRaises(ValueError):
            analysis.reduction(0, 1)

    def test_censored_readiness_is_not_dropped_to_produce_an_improvement(self):
        base = {'pair_id': 'one', 'arrival_validation_pass': True, 'integrity_pass': True,
                'readiness': {'outcome': 'ready', 'after_acceptance_seconds': 100}}
        censored = {**base, 'readiness': {'outcome': 'timeout'}}
        result = analysis.compare_groups([base], [censored], 'readiness_seconds', draws=10)
        self.assertFalse(result['all_ready'])
        self.assertNotIn('mean_reduction_percent', result)

    def test_pair_mismatch_cannot_be_silently_compared(self):
        with self.assertRaises(AssertionError):
            analysis.compare_groups([{'pair_id': 'one'}], [{'pair_id': 'two'}], 'readiness_seconds')

    def test_paired_reduction_keeps_baseline_and_all_repetitions(self):
        rows = [{'pair_id': str(i), 'arrival_validation_pass': True, 'integrity_pass': True,
                 'readiness': {'outcome': 'ready', 'after_acceptance_seconds': value}}
                for i,value in enumerate([100, 110, 90])]
        after = [{**r, 'readiness': {**r['readiness'], 'after_acceptance_seconds': r['readiness']['after_acceptance_seconds']/2}} for r in rows]
        result = analysis.compare_groups(rows, after, 'readiness_seconds', draws=100)
        self.assertEqual(result['mean_reduction_percent'], 50)
        self.assertEqual(result['independent_pairs'], 3)


if __name__ == '__main__':
    unittest.main()
