from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
from threading import Thread
from time import sleep

from redis import Redis
from sqlalchemy import func, or_

from app.db.models import OperationState, PipelineJob, VideoSearchChunk
from app.db.session import create_session
from app.services.maintenance_service import collect_artifacts, repair_indexes, record_operation
from app.services.qdrant_service import get_qdrant_client
from app.services.storage_service import get_storage
from app.services.lexical_index import LexicalIndex
from app.services.lexical_reconciliation import reconcile_lexical_index
from app.db import search_repository
from app.db.models import SearchProjection, SearchAttempt

logger = logging.getLogger(__name__)


def operational_metrics(db, *, now=None):
    now = now or datetime.utcnow()
    values = {"database_up": 1, "pending_age_seconds": 0, "expired_work": 0,
              "recent_failures": 0, "progress_age_seconds": 0}
    for model in (PipelineJob, VideoSearchChunk):
        oldest = db.query(func.min(func.coalesce(model.next_retry_at, model.created_at))).filter(
            model.status.in_(["pending", "retrying"]),
            or_(model.next_retry_at.is_(None), model.next_retry_at <= now)).scalar()
        if oldest:
            values["pending_age_seconds"] = max(values["pending_age_seconds"], (now - oldest).total_seconds())
        values["expired_work"] += db.query(model).filter(model.status == "processing",
            or_(model.lease_expires_at <= now, model.deadline_at <= now)).count()
        values["recent_failures"] += db.query(model).filter(model.status == "failed",
            model.last_error_at >= now - timedelta(minutes=10)).count()
        oldest_progress = db.query(func.min(model.progress_at)).filter(model.status == "processing").scalar()
        if oldest_progress:
            values["progress_age_seconds"] = max(values["progress_age_seconds"], (now - oldest_progress).total_seconds())
    names = ["dispatcher", "garbage_collection", "index_repair", "dependencies", "backup"]
    if search_repository.enabled():
        names.append("search_projections")
        for state in ("pending", "indexing", "retrying", "failed", "ready", "obsolete"):
            values[f"search_projection_{state}"] = db.query(SearchProjection).filter_by(status=state).count()
        values["search_cleanup_errors"] = db.query(SearchAttempt).filter(SearchAttempt.error_code.isnot(None)).count()
    if os.getenv("ELASTICSEARCH_URL") and not search_repository.enabled():
        names.append("lexical_index")
    for name in names:
        row = db.get(OperationState, name)
        values[f"{name}_success_age_seconds"] = max(0, (now - row.succeeded_at).total_seconds()) if row and row.succeeded_at else 1e9
        values[f"{name}_error"] = int(row is None or row.error is not None)
    return values


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/metrics", "/health"}:
            self.send_error(404)
            return
        db = create_session()
        try:
            values = operational_metrics(db)
            healthy = values["dispatcher_success_age_seconds"] < 120
            body = "".join(f"vindex_{name} {value}\n" for name, value in values.items()).encode()
            self.send_response(200 if self.path == "/metrics" or healthy else 503)
        except Exception:
            self.send_response(503)
            body = b"vindex_database_up 0\n"
        finally:
            db.close()
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def run_once():
    storage, qdrant = get_storage(), get_qdrant_client()
    operations = [
        ("garbage_collection", lambda db: collect_artifacts(db, storage, qdrant)),
        ("index_repair", lambda db: repair_indexes(db, storage, qdrant)),
        ("dependencies", lambda db: check_dependencies(storage, qdrant)),
    ]
    if os.getenv("ELASTICSEARCH_URL") and not search_repository.enabled():
        operations.append(("lexical_index", lambda db: reconcile_lexical(db, storage)))
    if search_repository.enabled():
        from app.services.search_indexing import reconcile
        operations.append(("search_projections", lambda db: reconcile(db, storage)))
    for name, operation in operations:
        db = create_session()
        try:
            result = operation(db)
            record_operation(db, name, details=result,
                error="Some items failed; see operation details" if result.get("errors") else None)
        except Exception as exc:
            logger.exception("Maintenance operation failed: %s", name)
            db.rollback()
            record_operation(db, name, error=type(exc).__name__)
        finally:
            db.close()


def reconcile_lexical(db, storage):
    elastic = LexicalIndex()
    try:
        return reconcile_lexical_index(db, storage, elastic,
            limit=max(1, int(os.getenv("ELASTICSEARCH_RECONCILE_BATCH", "10"))))
    finally:
        elastic.close()


def check_dependencies(storage, qdrant):
    storage.client.head_bucket(Bucket=storage.bucket)
    qdrant.get_collections()
    client = Redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"), socket_timeout=5, socket_connect_timeout=5)
    try:
        client.ping()
    finally:
        client.close()
    return {"s3": "ok", "qdrant": "ok", "redis": "ok"}


def main():
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("OPERATIONS_PORT", "9101"))), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Maintenance cycle failed")
        sleep(max(10, int(os.getenv("MAINTENANCE_INTERVAL_SECONDS", "60"))))


if __name__ == "__main__":
    main()
