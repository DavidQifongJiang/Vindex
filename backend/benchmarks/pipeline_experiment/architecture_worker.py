"""Warm every identical worker process before timing any architecture."""
import os
import time
from guard import validate_environment
from architecture_control import configure_worker

validate_environment(os.environ)
WORKER_CONFIGURATION = configure_worker()
from app.workers.celery_app import celery_app


@celery_app.task(name='architecture_warm')
def warm_models():
    from app.services.video_service import get_whisper_model
    from app.services.search_service import get_embedding_model
    get_whisper_model()
    get_embedding_model()
    print('ARCHITECTURE_MODELS_WARM pid=' + str(os.getpid()), flush=True)
    time.sleep(5)  # Occupy each pool slot so the four warmup tasks reach all children.
    return {'pid': os.getpid(), **WORKER_CONFIGURATION}
