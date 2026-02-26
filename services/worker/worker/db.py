import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Patch psycopg2 for gevent compatibility.
# This MUST happen before any database connections are created.
# It's safe to call in prefork workers too (no-op if gevent isn't active).
try:
    from psycogreen.gevent import patch_psycopg
    patch_psycopg()
except ImportError:
    pass  # psycogreen not installed — not using gevent pool

_engine = create_engine(
    os.environ.get(
        "DATABASE_URL",
        "postgresql://ttwatch_worker:changeme@postgres:5432/ttwatch",
    ),
    pool_size=5,
    max_overflow=5,
)
_SessionFactory = sessionmaker(bind=_engine)


@contextmanager
def db_session() -> Session:
    """Synchronous session for Celery worker tasks."""
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
