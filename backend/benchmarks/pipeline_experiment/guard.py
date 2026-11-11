"""Fail closed before loading any application code or using experiment services."""
from sqlalchemy.engine import make_url


def validate_environment(env):
    url = make_url(env.get('DATABASE_URL', 'sqlite://'))
    if (env.get('VINDEX_PIPELINE_EXPERIMENT') != 'true' or url.database != 'vindex_pipeline_bench'
            or url.host != 'bench-postgres' or env.get('QDRANT_URL') != 'http://bench-qdrant:6333'
            or env.get('CELERY_BROKER_URL') != 'redis://bench-redis:6379/0'
            or not env.get('S3_BUCKET_NAME', '').startswith('vindex-pipeline-bench-585572506964-')
            or len(env.get('VINDEX_BENCH_TOKEN', '')) < 32 or env.get('AUTH_REQUIRED') != 'false'):
        raise RuntimeError('Refusing non-isolated pipeline experiment')
