import unittest

from benchmarks.pipeline_experiment.guard import validate_environment
from benchmarks.provision_pipeline_experiment import validate_state, ACCOUNT, IMAGES


class PipelineExperimentSafety(unittest.TestCase):
    def setUp(self):
        self.env={'DATABASE_URL':'postgresql://bench:test@bench-postgres/vindex_pipeline_bench',
                  'VINDEX_PIPELINE_EXPERIMENT':'true','QDRANT_URL':'http://bench-qdrant:6333',
                  'CELERY_BROKER_URL':'redis://bench-redis:6379/0','AUTH_REQUIRED':'false',
                  'S3_BUCKET_NAME':'vindex-pipeline-bench-585572506964-1234567890','VINDEX_BENCH_TOKEN':'x'*40}

    def test_guard_accepts_only_isolated_service_configuration(self):
        validate_environment(self.env)
        for key,value in [('DATABASE_URL','postgresql://bench:test@production/vindex'),
                          ('S3_BUCKET_NAME','vindex-media-585572506964-us-east-1'),
                          ('QDRANT_URL','http://production:6333'),('CELERY_BROKER_URL','redis://production/0'),
                          ('VINDEX_PIPELINE_EXPERIMENT','false'),('VINDEX_BENCH_TOKEN','short')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_environment({**self.env,key:value})

    def test_journal_cannot_target_shared_bucket_or_unpinned_image(self):
        state={'account':ACCOUNT,'name':'vindex-pipeline-bench-1234567890',
               'bucket':f'vindex-pipeline-bench-{ACCOUNT}-1234567890','image':IMAGES['APP_IMAGE']}
        validate_state(state)
        for key,value in [('account','000000000000'),('name','vindex'),
                          ('bucket','vindex-media-585572506964-us-east-1'),('image','vindex:latest')]:
            with self.subTest(key=key), self.assertRaises(ValueError): validate_state({**state,key:value})
