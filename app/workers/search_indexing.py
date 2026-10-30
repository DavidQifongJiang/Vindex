"""Celery entrypoint and explicit operator repair command."""
import argparse

from app.db.search_repository import enabled, request_repair
from app.db.session import create_session
from app.services.search_indexing import process_projection
from app.services.storage_service import get_storage
from app.workers.celery_app import celery_app


@celery_app.task(name="index_search_projection", soft_time_limit=1800, time_limit=1830)
def index_search_projection(job_id):
    if not enabled():
        return {"status": "disabled"}
    return process_projection(job_id, get_storage())


def main():
    parser = argparse.ArgumentParser(description="Explicitly requeue a failed search projection after repairing its cause")
    parser.add_argument("--repair", required=True, metavar="JOB_UUID")
    args = parser.parse_args()
    with create_session() as db:
        if not request_repair(db, args.repair):
            parser.error("Job does not exist or is not repairable in its current state")
    print("Repair queued in Postgres")


if __name__ == "__main__":
    main()
