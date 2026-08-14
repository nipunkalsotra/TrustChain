"""
db/engine.py — lazy async engine + session factory.

Same lazy-singleton discipline as config.get_settings(): never construct the
engine at import time, only on first real use, so tests can set
DATABASE_URL / DATABASE_USE_NULL_POOL before anything touches Postgres.
"""

from functools import lru_cache
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    kwargs: dict = {"pool_pre_ping": True}
    if settings.database_use_null_pool:
        kwargs = {"poolclass": NullPool}
    return create_async_engine(settings.database_url, **kwargs)


_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def create_all_tables() -> None:
    """Create tables directly from the ORM models. Used by test setup and
    local dev bootstrap — real deployments apply the Alembic migrations
    instead (alembic/ directory), so schema history stays reviewable."""
    from db.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def truncate_all_tables() -> None:
    """Test isolation helper — empties every table without dropping schema."""
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE users, organizations, projects, memberships, api_keys, "
                "idempotency_keys, audit_events, refresh_tokens, runs, steps, anchor_outbox, "
                "anchor_batches, rm_scores, indexer_cursor RESTART IDENTITY CASCADE"
            )
        )
