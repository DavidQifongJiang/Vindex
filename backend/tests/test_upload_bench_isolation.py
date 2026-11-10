"""Safety gates for the opt-in benchmark wrapper (no cloud access)."""
import unittest
import tempfile
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

from benchmarks.upload_bench_server import validate_environment
from benchmarks.provision_upload_bench import validate_resource_journal, verify_preserved_evidence, ACCOUNT, IMAGE
from benchmarks.download_upload_evidence import evidence_path, verify_manifest
from benchmarks import provision_upload_bench


class UploadBenchmarkIsolationTests(unittest.TestCase):
    def setUp(self):
        self.env = {"VINDEX_BENCHMARK_ISOLATED": "true",
                    "DATABASE_URL": "postgresql://bench:example@bench-postgres/vindex_upload_bench",
                    "S3_BUCKET_NAME": "vindex-upload-bench-example", "VINDEX_BENCH_TOKEN": "x" * 40,
                    "AUTH_REQUIRED": "false"}

    def test_accepts_explicit_isolated_environment(self):
        validate_environment(self.env)

    def test_refuses_production_database_or_bucket(self):
        for key, value in [("DATABASE_URL", "postgresql://vindex:example@postgres/vindex"),
                           ("DATABASE_URL", "postgresql://bench:example@shared/vindex_upload_bench"),
                           ("S3_BUCKET_NAME", "vindex-media-production")]:
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                validate_environment({**self.env, key: value})

    def test_refuses_missing_enable_or_secret(self):
        for key, value in [("VINDEX_BENCHMARK_ISOLATED", "false"), ("VINDEX_BENCH_TOKEN", ""), ("AUTH_REQUIRED", "true")]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_environment({**self.env, key: value})

    def test_resource_journal_cannot_target_other_names_or_images(self):
        state = {'name': 'vindex-upload-bench-123456789a', 'account': ACCOUNT, 'image': IMAGE}
        validate_resource_journal(state)
        for key, value in [('account', 'different-account'), ('name', 'vindex-api'),
                           ('bucket', 'vindex-media-production'), ('role', 'vindex-task'),
                           ('profile', 'vindex-state-host'), ('image', 'unexpected; command')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_resource_journal({**state, key: value})

    def test_evidence_paths_cannot_escape_or_overwrite_another_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ['../outside.json','/absolute.json','primary/../../outside.json','primary\\outside.json']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    evidence_path(root,name)

    def test_manifest_detects_truncation_and_changed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'report.json';path.write_bytes(b'complete')
            manifest={'files':{'report.json':{'bytes':8,'sha256':hashlib.sha256(b'complete').hexdigest()}}}
            verify_manifest(root,manifest)
            path.write_bytes(b'corrupt!')
            with self.assertRaises(ValueError):verify_manifest(root,manifest)

    def test_teardown_rechecks_run_identity_and_preserved_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'report.json';path.write_bytes(b'complete')
            manifest={'files':{'report.json':{'bytes':8,'sha256':hashlib.sha256(b'complete').hexdigest()}}}
            (root/'archive-complete.json').write_text(json.dumps(manifest))
            state={'bucket':'test-run-bucket','archive_prefix':'evidence/remote-001/'}
            proof={'bucket':state['bucket'],'prefix':state['archive_prefix'],
                   'archive_hashes_verified':True,'local_directory':str(root)}
            marker=root/'evidence-verified.json';marker.write_text(json.dumps(proof))
            verify_preserved_evidence(state,root)
            marker.write_text(json.dumps({**proof,'bucket':'different-run'}))
            with self.assertRaises(ValueError):verify_preserved_evidence(state,root)
            marker.write_text(json.dumps(proof));path.write_bytes(b'corrupt!')
            with self.assertRaises(ValueError):verify_preserved_evidence(state,root)

    def test_teardown_preserves_hosts_when_unarchived_inputs_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'name': 'vindex-upload-bench-123456789a', 'account': ACCOUNT,
                     'bucket': f'vindex-upload-bench-{ACCOUNT}-123456789a',
                     'instance': 'test-api', 'runner_instance': 'test-runner',
                     'archive_prefix': 'evidence/remote-001/'}
            (root / 'resources.json').write_text(json.dumps(state))
            clients = {name: Mock() for name in ['sts', 'ec2', 'iam', 's3']}
            clients['sts'].get_caller_identity.return_value = {'Account': ACCOUNT}
            clients['s3'].list_objects_v2.return_value = {'KeyCount': 1}
            session = Mock()
            session.client.side_effect = clients.__getitem__
            arguments = ['provision_upload_bench.py', 'destroy', '--account', ACCOUNT,
                         '--output', str(root)]
            with patch('sys.argv', arguments), patch.object(provision_upload_bench.boto3, 'Session', return_value=session), \
                    patch.object(provision_upload_bench, 'verify_preserved_evidence'):
                with self.assertRaisesRegex(SystemExit, 'before any teardown'):
                    provision_upload_bench.main()
            clients['ec2'].terminate_instances.assert_not_called()
            clients['s3'].delete_objects.assert_not_called()
            clients['iam'].delete_role.assert_not_called()


if __name__ == '__main__':
    unittest.main()
