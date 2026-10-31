import os

from celery import Celery

BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
VISIBILITY_TIMEOUT_SECONDS = int(os.getenv("CELERY_VISIBILITY_TIMEOUT_SECONDS", "7200"))
PROCESSING_DEADLINE_SECONDS = max(1, int(os.getenv("PROCESSING_DEADLINE_SECONDS", "1800")))

celery_app = Celery(
    "vindex",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.workers.task", "app.workers.search_indexing"],
)

celery_app.conf.update(
    broker_transport_options={
        "visibility_timeout": VISIBILITY_TIMEOUT_SECONDS,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
    result_backend_transport_options={
        "visibility_timeout": VISIBILITY_TIMEOUT_SECONDS,
    },
    task_acks_late=True,
    task_soft_time_limit=PROCESSING_DEADLINE_SECONDS,
    task_time_limit=PROCESSING_DEADLINE_SECONDS + 30,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=5,
    task_routes={
        "index_search_projection": {"queue": "indexing"},
        "process_video_search_task": {"queue": "search"},
        "process_video_search_chunk_task": {"queue": "search"},
        "finalize_video_search_task": {"queue": "search"},
        "process_video_playback_task": {"queue": "playback"},
        "process_video_task": {"queue": "celery"},
    },
)
