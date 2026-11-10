"""Safety gates for the opt-in benchmark wrapper (no cloud access)."""
import unittest

from benchmarks.upload_bench_server import validate_environment
from benchmarks.provision_upload_bench import validate_resource_journal, ACCOUNT, IMAGE


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


if __name__ == '__main__':
    unittest.main()
