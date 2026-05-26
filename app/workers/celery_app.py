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

celery_app = Celery(
    "vindex",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.workers.task"],
)
