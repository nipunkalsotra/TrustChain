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

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from db import tenancy
from db.engine import get_sessionmaker, rls_bypass
from db.models import Run, Step, User

_PBKDF2_ITERATIONS = 260_000
_argon2_hasher = PasswordHasher()


# ── Password hashing — Argon2id, with transparent PBKDF2 migration ─────────
# New hashes use Argon2id (memory-hard — the OWASP-recommended default,
# unlike PBKDF2 which is only CPU-hard and so cheaper to brute-force on
# GPUs/ASICs). Existing users' PBKDF2-HMAC-SHA256 hashes (Phase 1, stdlib
# only) still verify correctly — `verify_password` dispatches on the
# stored hash's own format (Argon2's PHC string always starts with
# "$argon2"; the old format never contains "$argon2") — and are
# transparently upgraded to Argon2id in place on next successful login
# (see authenticate_user), so no forced reset/migration script is needed
# and no one is ever locked out mid-rollout.

def hash_password(password: str) -> str:
    return _argon2_hasher.hash(password)


def _hash_password_pbkdf2(password: str) -> str:
    """Only used by tests exercising the legacy-hash migration path."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def _verify_password_pbkdf2(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("$argon2"):
        try:
            return _argon2_hasher.verify(stored, password)
        except VerifyMismatchError:
            return False
    return _verify_password_pbkdf2(password, stored)


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

        if not user.password_hash.startswith("$argon2"):
            # Transparent migration: the password is only ever available
            # in cleartext right here, right after a successful legacy
            # verify — rehash it into Argon2id now rather than running a
            # separate backfill script (which would need the cleartext
            # password too, i.e. can't be done offline at all).
            user.password_hash = hash_password(password)
            await session.commit()

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


async def fail_run_if_still_running(run_id: str, message: str, completed_at: int) -> bool:
    """Same terminal-state write as fail_run(), but guarded by `WHERE
    status = 'running'` and reporting whether it actually changed
    anything — used by main.py's shutdown drain (F14) to mark an
    abandoned in-flight run as failed WITHOUT a chance of clobbering a run
    that raced to a real completion in the same narrow window (the
    cancelled task's own finally-block could theoretically still be
    writing complete_run()/fail_run() concurrently with shutdown calling
    this). fail_run()/complete_run() themselves stay blind UPDATEs — every
    other caller already owns the only write to a given run's terminal
    status, so the extra guard would be pure overhead there.

    Wrapped in rls_bypass(): called from main.py's lifespan shutdown, not
    from inside a request, so there is no Principal-derived
    current_project_id ContextVar set for this write's session to pick
    up — under the api service's RLS-bound `trustchain_api` role (see
    alembic/versions/9f3a1c7d5e2b), that meant this UPDATE silently
    matched zero rows every time (RLS policies fail closed, not an
    error) rather than actually recording the abandoned run, found by
    testing a real SIGTERM against a real running container rather than
    only against pytest's superuser-role test database, which bypasses
    RLS unconditionally and so never exercised this path. Justified the
    same way read_model.get_platform_stats' cross-tenant read is: a
    genuinely tenant-agnostic, server-initiated operation, not a gap in
    per-tenant isolation for real request traffic."""
    from sqlalchemy import update

    session_factory = get_sessionmaker()
    with rls_bypass():
        async with session_factory() as session:
            stmt = (
                update(Run)
                .where(Run.run_id == run_id, Run.status == "running")
                .values(status="error", result_json=json.dumps({"message": message}), completed_at=completed_at)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0


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


async def get_or_create_run_for_project(run_id: str, project_id: int, created_at: int) -> bool:
    """For POST /steps (SDK ingest of a third-party agent's own step,
    plan §7.4/§13.4) — unlike POST /run-agent, that workflow has no
    separate "create a run" call; a caller just starts logging steps
    under a run_id it picked itself. Creates a minimal Run row (task=NULL
    — there's no task text for a step the caller ran itself, only for
    TrustChain's own pipeline) the first time a run_id is seen.

    Returns True if run_id belongs to (or was just created for) this
    project — safe to write a Step against it. Returns False if run_id
    already exists under a DIFFERENT project — the caller must reject
    that (404), never silently write into another tenant's run.

    Uses ON CONFLICT DO NOTHING, not create_run()'s ON CONFLICT DO
    UPDATE: that upsert only ever updates task/user_email (never
    project_id), so it's safe for its own use (a client-supplied run_id
    colliding with another project there just leaves project_id
    unchanged) — but reusing it here would let a caller in project B
    silently overwrite project A's task/user_email fields on task/
    user_email columns this endpoint doesn't even touch. Insert-if-absent
    then check ownership is the actually-safe primitive for this case.
    """
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = pg_insert(Run).values(
            run_id=run_id, project_id=project_id, task=None, user_email=None,
            status="running", created_at=created_at,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[Run.run_id])
        await session.execute(stmt)
        await session.commit()

        run = await session.get(Run, run_id)
        return run is not None and run.project_id == project_id


async def next_step_index(run_id: str) -> int:
    """Server-computed, not caller-supplied — a third-party SDK calling
    POST /steps repeatedly shouldn't have to track its own step counter
    (and a wrong/reused index from a buggy client would corrupt the
    Merkle leaf ordering steps.step_index is part of)."""
    from sqlalchemy import func

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(Step).where(Step.run_id == run_id)
        )
        return result.scalar_one()


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


async def get_applied_migration_version() -> Optional[str]:
    """The `alembic_version` table's current value (F15, GET /ready) —
    None if that table doesn't exist at all, which is a real, expected
    state distinct from a genuine version mismatch: tests/conftest.py's
    `_schema` fixture and any dev DB stood up before its first
    `alembic upgrade head` build the schema straight from the ORM models
    (create_all_tables()), which never creates `alembic_version` — a real
    deployment always runs migrations before starting the app (see
    docs/release-process.md), so this table missing there would itself be
    the anomaly, not the normal case main.py's /ready needs to tolerate."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        try:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar_one_or_none()
        except ProgrammingError:
            await session.rollback()
            return None
