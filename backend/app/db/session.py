import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

engine = None
SessionLocal = None


def _init_engine():
    """Initialize the SQLAlchemy engine and session factory on first use.

    Uses ``DATABASE_URL`` when provided, otherwise builds the connection URL
    from individual Postgres environment variables
    (``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD``, ``PGDATABASE``)
    that Railway resolves automatically from the linked Postgres service.
    Falls back to local development defaults when those variables are absent.
    """
    global engine, SessionLocal

    if engine is not None:
        return

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        pghost = os.getenv("PGHOST", "localhost")
        pgport = os.getenv("PGPORT", "5432")
        pguser = os.getenv("PGUSER", "vindex")
        pgpassword = os.getenv("PGPASSWORD", "vindex")
        pgdatabase = os.getenv("PGDATABASE", "vindex")

        database_url = URL.create("postgresql+psycopg2", username=pguser, password=pgpassword,
                                 host=pghost, port=int(pgport), database=pgdatabase)
    else:
        database_url = make_url(database_url)

    connect_args = {}
    if database_url.get_backend_name() == "postgresql":
        connect_args = {"connect_timeout": 5, "options": f"{os.getenv('PGOPTIONS', '')} -c statement_timeout=30000 -c lock_timeout=5000"}
    engine = create_engine(database_url, pool_pre_ping=True, pool_timeout=10, connect_args=connect_args)
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
