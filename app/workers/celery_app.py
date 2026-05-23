from celery import Celery

celery_app = Celery(
    "vindex",
    broker="redis://localhost:6379/0",
    include=["app.workers.task"],
)
