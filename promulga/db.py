"""Database engine and session handling (SQLAlchemy 2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(database_url: str):
    """Creates the engine (once per process) and the tables if missing."""
    global _engine, _SessionLocal
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver detail
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    from . import migrate, models  # noqa: F401  (models registers the tables)
    migrate.upgrade(_engine)
    return _engine


def get_engine():
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for background jobs and scripts: commit on success, rollback on error."""
    assert _SessionLocal is not None, "init_engine() not called"
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the route itself."""
    assert _SessionLocal is not None, "init_engine() not called"
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
