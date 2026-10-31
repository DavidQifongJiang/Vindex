"""A subprocess used by the isolated Postgres crash-recovery test."""
import os
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import VideoSearchChunk
from app.db.pipeline_repository import claim_work


if __name__ == "__main__":
    engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"]).execution_options(
        schema_translate_map={None: os.environ["VINDEX_TEST_SCHEMA"]})
    db = sessionmaker(bind=engine)()
    try:
        row = claim_work(db, VideoSearchChunk, "v1", 0, "crash-worker")
        if row is None:
            raise RuntimeError("Crash worker could not claim its chunk")
        print(row.processing_token, flush=True)
    finally:
        db.close()
    Event().wait()
