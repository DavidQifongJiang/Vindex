import importlib.util
import json
import os
import runpy
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

SOURCE = Path(__file__).resolve().parents[1]/'benchmarks/pipeline_experiment'
sys.path.insert(0, str(SOURCE))
try:
    spec = importlib.util.spec_from_file_location('architecture_control', SOURCE/'architecture_control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    spec = importlib.util.spec_from_file_location('architecture_state', SOURCE/'architecture_state.py')
    publication = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publication)
    spec = importlib.util.spec_from_file_location('architecture_client', SOURCE/'mixed_client.py')
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
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
    def test_wrong_client_quota_cannot_launch_a_registered_trial(self):
        spec = {'upload_client_vcpu': 3.5, 'query_client_vcpu': .25}
        with patch.object(client, 'command') as command:
            with self.assertRaisesRegex(AssertionError, 'Upload client quota'):
                client.run(spec, {'upload_client_cpus': '3', 'query_client_cpus': '.25'})
            with self.assertRaisesRegex(AssertionError, 'Query client quota'):
                client.run(spec, {'upload_client_cpus': '3.5', 'query_client_cpus': '.5'})
        command.assert_not_called()

    def test_client_restart_preserves_successful_and_failed_prior_trials(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            settings = {'bucket': 'isolated'}
            self.assertFalse(client.publish_recorded(folder, settings))
            for finished in [True, False]:
                original = json.dumps({'finished': finished})
                (folder/'client-result.json').write_text(original)
                with patch.object(client, 'command') as command:
                    self.assertTrue(client.publish_recorded(folder, settings))
                self.assertEqual(command.call_count, 2)
                self.assertIn('sync', command.call_args_list[0].args[0])
                self.assertIn('cp', command.call_args_list[1].args[0])
                self.assertEqual((folder/'client-result.json').read_text(), original)

    def test_legacy_search_artifacts_must_match_every_indexed_segment(self):
        segments = [{'start': 0, 'end': 4, 'text': 'vector search'},
                    {'start': 895, 'end': 899, 'text': 'last segment'}]
        points = [SimpleNamespace(payload=s) for s in segments]
        publication.validate_legacy_cardinality(segments, segments, points)
        with self.assertRaises(AssertionError):
            publication.validate_legacy_cardinality(segments, segments[:1], points[:1])
        with self.assertRaises(AssertionError):
            publication.validate_legacy_cardinality(segments, segments, points[:1] * 2)

    def test_legacy_identity_allows_serialization_roundoff_but_not_timestamp_changes(self):
        segments = [{'start': 108.39999999999999, 'end': 111.75999999999999, 'text': 'same words'}]
        points = [SimpleNamespace(payload={'start': 108.4, 'end': 111.76, 'text': 'same words'})]
        publication.validate_legacy_cardinality(segments, segments, points)
        points[0].payload['start'] += .000001
        with self.assertRaises(AssertionError):
            publication.validate_legacy_cardinality(segments, segments, points)

    def test_completed_publication_cannot_skip_missing_artifact_checks(self):
        for segments, embeddings in [(None, 'embeddings'), ('segments', None), (None, None)]:
            with self.assertRaises(AssertionError):
                publication.completed_search_publication(SimpleNamespace(
                    embedding_status='completed', segments_path=segments, embedding_path=embeddings))
        self.assertFalse(publication.completed_search_publication(SimpleNamespace(
            embedding_status='pending', segments_path=None, embedding_path=None)))
        self.assertTrue(publication.completed_search_publication(SimpleNamespace(
            embedding_status='completed', segments_path='segments', embedding_path='embeddings')))

    def test_control_fails_closed_outside_isolated_environment(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            control.architecture()

    def test_warmup_rejects_nonisolated_environment_before_publishing(self):
        fake = MagicMock()
        with patch.dict(sys.modules, {'architecture_control': control, 'app.workers.celery_app': fake}), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                runpy.run_path(str(SOURCE/'architecture_warm.py'))
        fake.celery_app.send_task.assert_not_called()

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
                'upload_success_pass': True, 'query_ledger': {'success_fraction': 1},
                'phases': {'load': {'success_fraction': 1}},
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
                 'upload_success_pass': True, 'query_ledger': {'success_fraction': 1},
                 'phases': {'load': {'success_fraction': 1}},
                 'readiness': {'outcome': 'ready', 'after_acceptance_seconds': value}}
                for i,value in enumerate([100, 110, 90])]
        after = [{**r, 'readiness': {**r['readiness'], 'after_acceptance_seconds': r['readiness']['after_acceptance_seconds']/2}} for r in rows]
        result = analysis.compare_groups(rows, after, 'readiness_seconds', draws=100)
        self.assertEqual(result['mean_reduction_percent'], 50)
        self.assertEqual(result['independent_pairs'], 3)

    def test_empty_failed_upload_trial_has_no_objects_to_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, valid = analysis.verification_rows(Path(directory), {'logical_ids': []})
            self.assertEqual(rows, [])
            self.assertTrue(valid)
            rows, valid = analysis.verification_rows(Path(directory), {'logical_ids': ['committed-id']})
            self.assertFalse(valid)

    def test_failed_upload_workload_cannot_claim_faster_search(self):
        row = {'pair_id': 'one', 'arrival_validation_pass': True, 'integrity_pass': True,
               'upload_success_pass': False, 'query_ledger': {'success_fraction': 1}}
        result = analysis.compare_groups([row], [row], 'search_p95_seconds')
        self.assertFalse(result['all_planned_pairs_valid'])
        self.assertNotIn('mean_reduction_percent', result)

    def test_overall_success_does_not_hide_errors_during_ingestion(self):
        row = {'pair_id': 'one', 'arrival_validation_pass': True, 'integrity_pass': True,
               'upload_success_pass': True, 'query_ledger': {'success_fraction': .995},
               'phases': {'load': {'success_fraction': .98}}}
        result = analysis.compare_groups([row], [row], 'search_p95_seconds')
        self.assertFalse(result['all_planned_pairs_valid'])
        self.assertNotIn('mean_reduction_percent', result)

    def test_replaced_worker_child_invalidates_warm_pool(self):
        expected = [{'namespace_pid': i, 'start_ticks': 100} for i in range(10, 14)]
        self.assertTrue(analysis.same_pool(expected, expected))
        changed = expected[:3] + [{'namespace_pid': 14, 'start_ticks': 101}]
        self.assertFalse(analysis.same_pool(expected, changed))
        recycled = expected[:3] + [{'namespace_pid': 13, 'start_ticks': 102}]
        self.assertFalse(analysis.same_pool(expected, recycled))

    def test_aborted_trial_keeps_unreturned_commits_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'reconciliation.json').write_text(json.dumps({'unreturned_commits': ['lost-ack'], 'logical_ids': ['lost-ack']}))
            spec = {'name': 'failed-b', 'uploads_per_minute': 3, 'stage': 'b', 'architecture': 'branches',
                    'mode': 'api', 'repeat': 1, 'pair_id': 'one'}
            row = analysis.incomplete_trial(folder, spec, {'failure_type': 'AssertionError', 'failure': 'unreturned commit'})
            self.assertEqual(row['trial_state'], 'aborted')
            self.assertEqual(row['reconciliation']['unreturned_commits'], ['lost-ack'])
            self.assertFalse(row['integrity_pass'])
            self.assertIn('verification.json', row['missing_evidence'])


if __name__ == '__main__':
    unittest.main()
