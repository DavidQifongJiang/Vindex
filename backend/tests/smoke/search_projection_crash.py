"""Disposable Postgres test subprocess: simulate process death after durable claim."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.search_repository import claim

if __name__ == "__main__":
    engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"],
                           connect_args={"options": "-csearch_path=" + os.environ["VINDEX_TEST_SCHEMA"]})
    with Session(engine) as db:
        assert claim(db, os.environ["VINDEX_TEST_JOB"])
    os._exit(23)
