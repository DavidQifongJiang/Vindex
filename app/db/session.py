import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = None
SessionLocal = None


def _init_engine():
    """Initialize the SQLAlchemy engine and session factory on first use.

    Defers engine creation until this function is called so that Railway
    reference variables (e.g. ``${{ Postgres.DATABASE_URL }}``) are fully
    resolved before SQLAlchemy receives the connection string.
    """
    global engine, SessionLocal

    if engine is not None:
        return

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://vindex:vindex@localhost:5432/vindex",
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
