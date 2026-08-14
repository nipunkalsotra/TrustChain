"""
db/engine.py — lazy async engine + session factory.

Same lazy-singleton discipline as config.get_settings(): never construct the
engine at import time, only on first real use, so tests can set
DATABASE_URL / DATABASE_USE_NULL_POOL before anything touches Postgres.

RLS CONTEXT PROPAGATION (see alembic/versions/*_row_level_security.py):
runs/steps/api_keys/idempotency_keys/audit_events carry Postgres Row-Level
Security policies keyed on the `app.current_project_id`/`app.current_org_id`
session GUCs — a second, DB-enforced layer under invariant I7 ("no tenant
can read or write another tenant's data") that holds even if some future
endpoint's Python code forgets a project_id filter. Those GUCs need to be
set at the start of every transaction the API opens, without touching each
of db/__init__.py's/read_model.py's ~30 individual call sites — so this
module sets them via a SQLAlchemy `"begin"` engine event instead, reading
from the two ContextVars below (set once per request, in auth.py's
get_current_principal/get_current_user — see their docstrings).

Only the API service's Postgres role (`trustchain_api`, see the migration)
is actually subject to these policies. anchor-worker/indexer/alembic all
connect as the `trustchain` superuser, which bypasses RLS unconditionally
per Postgres semantics — so for them this event listener fires but is a
guaranteed no-op (their ContextVars are never set, since nothing in those
processes ever calls auth.get_current_principal).
"""

import contextlib
import contextvars
from functools import lru_cache
from typing import Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import get_settings

current_project_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("current_project_id", default=None)
current_org_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("current_org_id", default=None)
_rls_bypass: contextvars.ContextVar[bool] = contextvars.ContextVar("rls_bypass", default=False)


@contextlib.contextmanager
def rls_bypass():
    """For the rare, deliberately-cross-tenant query (today: just
    read_model.get_platform_stats' aggregate counts) — sets
    `app.rls_bypass`, which every RLS policy also ORs against. Narrow and
    grep-able on purpose: every caller of this is a place that's read and
    justified its need for cross-tenant visibility, not a blanket escape
    hatch."""
    token = _rls_bypass.set(True)
    try:
        yield
    finally:
        _rls_bypass.reset(token)


def _set_rls_session_context(conn, *_args) -> None:
    project_id = current_project_id.get()
    if project_id is not None:
        conn.execute(text("SELECT set_config('app.current_project_id', :v, true)"), {"v": str(project_id)})
    org_id = current_org_id.get()
    if org_id is not None:
        conn.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})
    if _rls_bypass.get():
        conn.execute(text("SELECT set_config('app.rls_bypass', 'true', true)"))


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    kwargs: dict = {"pool_pre_ping": True}
    if settings.database_use_null_pool:
        kwargs = {"poolclass": NullPool}
    engine = create_async_engine(settings.database_url, **kwargs)
    event.listen(engine.sync_engine, "begin", _set_rls_session_context)
    return engine


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
