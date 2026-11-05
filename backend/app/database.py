"""Synchronous SQLAlchemy 2.x engine, session factory, and declarative base.

Design notes
------------
- The engine is created lazily at first use (via ``get_engine()``), not at
  module import time, so importing this module never makes a network call.
- ``check_database_connectivity()`` is called from the application lifespan
  hook and provides an early, explicit error if the database is unreachable
  rather than failing silently on the first request.
- The session is synchronous. FastAPI route handlers that call ``get_db()``
  should be declared ``async def``; SQLAlchemy will block their thread while
  executing SQL, but FastAPI runs sync dependencies in a thread-pool so this
  does not block the main event loop.
"""

import logging
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Column, MetaData, Table, Uuid, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base shared by application ORM models."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "%(constraint_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# Supabase owns this table. Keeping its primary-key definition in metadata lets
# SQLAlchemy resolve Profile.id's foreign key without creating an Auth model.
supabase_auth_users = Table(
    "users",
    Base.metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    schema="auth",
)


@lru_cache(maxsize=1)
def get_engine():
    """Return (and cache) the SQLAlchemy engine.

    Creating the engine here — rather than at import time — means test modules
    can patch ``settings.database_url`` before the engine is ever constructed.
    ``pool_pre_ping=True`` detects stale PgBouncer connections before use.
    """
    if settings.database_url_is_default:
        logger.warning(
            "DATABASE_URL is using the localhost default. "
            "Database operations will fail unless a local PostgreSQL instance "
            "is running. Set DATABASE_URL in backend/.env for Supabase."
        )
    return create_engine(settings.database_url, pool_pre_ping=True)


# Module-level convenience alias kept for compatibility with existing imports.
# Any code that calls ``engine.connect()`` directly should use ``get_engine()``
# so it benefits from the lazy initialisation.
engine = get_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def check_database_connectivity() -> None:
    """Execute a cheap query to verify the database is reachable.

    Call this from the FastAPI lifespan ``startup`` hook so the application
    fails fast with a clear error message rather than serving requests that
    will all fail on their first DB call.

    Raises ``RuntimeError`` if the database cannot be reached.
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connectivity check passed.")
    except OperationalError as exc:
        raise RuntimeError(
            f"Database is not reachable at startup. "
            f"Check DATABASE_URL in backend/.env. Original error: {exc}"
        ) from exc


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields one SQLAlchemy session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
