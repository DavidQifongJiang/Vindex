"""Safety gates for the opt-in benchmark wrapper (no cloud access)."""
import unittest
import tempfile
import hashlib
import json
from pathlib import Path

from benchmarks.upload_bench_server import validate_environment
from benchmarks.provision_upload_bench import validate_resource_journal, verify_preserved_evidence, ACCOUNT, IMAGE
from benchmarks.download_upload_evidence import evidence_path, verify_manifest


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


if __name__ == '__main__':
    unittest.main()
