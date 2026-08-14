"""
db package — persistence for TrustChain (Postgres, async SQLAlchemy).

Public surface is unchanged from the Phase-1 SQLite implementation on
purpose: `import db; db.create_user(...)` etc. still works exactly the same
way, with the same function names, signatures, and returned dict shapes.
Only the storage engine underneath changed — main.py and the test suite
needed zero changes to their calling code (see the Phase 2 plan's write-up
on why this was worth preserving).

Multi-tenancy (db/tenancy.py) changed create_user/create_run's *behavior*
without changing their *call signatures* where possible: create_user now
also provisions an Organization/Project/Membership per new user (see its
docstring), and create_run/list_runs/get_run are project-scoped. This is
what makes invariant I7 ("no tenant can read or write another tenant's
runs, agents, or scores") real rather than just informally true because
nobody's built a second tenant yet.
"""

import hashlib
import hmac
import json
import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from db import tenancy
from db.engine import get_sessionmaker
from db.models import Run, User

_PBKDF2_ITERATIONS = 260_000


# ── Password hashing — PBKDF2-HMAC-SHA256, stdlib only ──────────────────────
# Unchanged from Phase 1 — pure Python, no SQL, no reason to touch it.

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


# ── Users ─────────────────────────────────────────────────────────────────

async def create_user(email: str, name: str, password: str, created_at: int) -> dict:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        user = User(email=email, name=name, password_hash=hash_password(password), created_at=created_at)
        session.add(user)
        try:
            await session.flush()  # populates user.id; surfaces the unique-email violation now
        except IntegrityError:
            await session.rollback()
            raise ValueError("email already registered")

        # Same transaction as the user row — a crash here must not leave a
        # user with no project, since every downstream principal resolution
        # assumes one always exists. See tenancy.provision_personal_org.
        project = await tenancy.provision_personal_org(session, user.id, name, created_at)
        await session.commit()

    return {"email": email, "name": name, "userId": user.id, "projectId": project.id, "orgId": project.org_id}


async def authenticate_user(email: str, password: str) -> Optional[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None

    project = await tenancy.get_default_project_for_user(user.id)
    if project is None:
        # Pre-multi-tenancy user row with no membership (shouldn't happen
        # post-migration — the backfill provisions one for every existing
        # user — but fail loudly rather than return a principal that can't
        # resolve a project, which every scoped query downstream assumes).
        raise RuntimeError(f"user {email!r} has no organization/project — data migration issue")

    return {"email": user.email, "name": user.name, "userId": user.id, **project}


# ── Runs ──────────────────────────────────────────────────────────────────

def _row_to_run_dict(run: Run) -> dict:
    return {
        "runId":       run.run_id,
        "projectId":   run.project_id,
        "task":        run.task,
        "userEmail":   run.user_email,
        "status":      run.status,
        "result":      json.loads(run.result_json) if run.result_json else None,
        "createdAt":   run.created_at,
        "completedAt": run.completed_at,
    }


async def create_run(run_id: str, project_id: int, task: str, user_email: Optional[str], created_at: int) -> None:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = pg_insert(Run).values(
            run_id=run_id, project_id=project_id, task=task, user_email=user_email,
            status="running", created_at=created_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Run.run_id],
            set_={"task": stmt.excluded.task, "user_email": stmt.excluded.user_email},
        )
        await session.execute(stmt)
        await session.commit()


async def _set_run_status(run_id: str, status: str, result: Optional[dict], completed_at: int) -> None:
    """A plain UPDATE, not an upsert — every real caller (main.py's
    _run_pipeline_background, agents/pipeline.py's smoke test) always
    calls create_run() first, so the row already exists. An upsert here
    would need a project_id in its INSERT clause purely to satisfy
    runs.project_id's NOT NULL constraint on a path that can never
    actually be taken (Postgres validates an INSERT ... ON CONFLICT's
    VALUES against NOT NULL constraints regardless of whether the
    conflict branch fires) — a plain UPDATE sidesteps that entirely."""
    from sqlalchemy import update

    session_factory = get_sessionmaker()
    result_json = json.dumps(result) if result is not None else None
    async with session_factory() as session:
        stmt = (
            update(Run)
            .where(Run.run_id == run_id)
            .values(status=status, result_json=result_json, completed_at=completed_at)
        )
        await session.execute(stmt)
        await session.commit()


async def complete_run(run_id: str, result: dict, completed_at: int) -> None:
    await _set_run_status(run_id, "complete", result, completed_at)


async def fail_run(run_id: str, message: str, completed_at: int) -> None:
    await _set_run_status(run_id, "error", {"message": message}, completed_at)


async def get_run(run_id: str, project_id: int) -> Optional[dict]:
    """Scoped by project_id (invariant I7) — a run_id from another
    tenant's project returns None, same as a run_id that doesn't exist at
    all, so a caller can't distinguish "not yours" from "never existed" by
    probing IDs."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is not None and run.project_id != project_id:
            return None
    return _row_to_run_dict(run) if run else None


async def list_runs(project_id: int, limit: int = 50) -> list[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        # created_at is nullable in principle (never in practice — every
        # caller passes it) — COALESCE keeps ordering well-defined either way.
        from sqlalchemy import func
        stmt = (
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(func.coalesce(Run.created_at, 0).desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        runs = result.scalars().all()
    return [_row_to_run_dict(r) for r in runs]


async def ping() -> None:
    """Trivial liveness query for GET /ready — deliberately not tenant-
    scoped (there is no principal at that point), just "is the database
    reachable and able to execute a query." Raises on failure; callers
    treat any exception as not-ready."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from sqlalchemy import text
        await session.execute(text("SELECT 1"))
