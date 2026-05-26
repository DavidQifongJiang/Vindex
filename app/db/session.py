import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = None
SessionLocal = None


def _init_engine():
    """Initialize the SQLAlchemy engine and session factory on first use.

    Builds the connection URL from individual Postgres environment variables
    (``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD``, ``PGDATABASE``)
    that Railway resolves automatically from the linked Postgres service.
    Falls back to local development defaults when those variables are absent.
    """
    global engine, SessionLocal

    if engine is not None:
        return

    pghost = os.getenv("PGHOST", "localhost")
    pgport = os.getenv("PGPORT", "5432")
    pguser = os.getenv("PGUSER", "vindex")
    pgpassword = os.getenv("PGPASSWORD", "vindex")
    pgdatabase = os.getenv("PGDATABASE", "vindex")

    database_url = (
        f"postgresql+psycopg2://{pguser}:{pgpassword}@{pghost}:{pgport}/{pgdatabase}"
    )

    engine = create_engine(database_url)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )


def get_db():
    db = create_session()
    try:
        yield db
    finally:
        db.close()


def create_session():
    """Create a database session after lazily initialising the engine."""
    _init_engine()
    return SessionLocal()


def get_engine():
    """Return the (lazily initialised) SQLAlchemy engine."""
    _init_engine()
    return engine
