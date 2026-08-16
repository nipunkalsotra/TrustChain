"""
db/tenancy.py — organizations, projects, memberships, and API keys.

Every user gets an auto-provisioned personal Organization + default Project
+ owner Membership at signup (see db.create_user) — invisible to the
current web frontend (still just email/password login, same response
shapes), but real underneath: every run a user creates is scoped to their
project (invariant I7), not just informally tagged with user_email like
Phase 2.0-2.2. API keys are the machine-credential path for SDK/third-party
consumers — issued against a project, never a user directly.
"""

import hashlib
import secrets
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_sessionmaker
from db.models import ApiKey, Membership, Organization, Project

# Scopes an API key can hold — least-privilege by default (plan §11.3).
# "logs:write" covers SDK tc.log(); "runs:write" covers starting a pipeline
# run; "agents:register" covers SDK tc.register_agent(); the ":read" scopes
# cover their respective GET endpoints.
VALID_SCOPES = frozenset({"logs:write", "runs:read", "runs:write", "agents:register", "agents:read"})


async def provision_personal_org(session: AsyncSession, user_id: int, user_name: str, created_at: int) -> Project:
    """Creates Organization + default Project + owner Membership for a
    freshly-created user, all in the CALLER's transaction (so a crash
    between "user row committed" and "org/project/membership committed"
    is impossible — a user with no project would break every downstream
    invariant that assumes principal.project_id always resolves to
    something real). Returns the new Project; caller is responsible for
    flushing/committing the session."""
    org = Organization(name=f"{user_name}'s Organization", plan="free", gas_spent_wei=0, created_at=created_at)
    session.add(org)
    await session.flush()

    project = Project(org_id=org.id, name="Default", environment="live", created_at=created_at)
    session.add(project)
    await session.flush()

    session.add(Membership(user_id=user_id, org_id=org.id, role="owner", created_at=created_at))
    await session.flush()

    return project


async def get_default_project_for_user(user_id: int) -> Optional[dict]:
    """Resolves "the" project for a user — today every user has exactly
    one org (provisioned at signup) with exactly one project, so "first
    membership's first project" is unambiguous. Multiple orgs/projects per
    user is representable in the schema (for later, once there's a UI to
    pick one) but not yet exposed."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(Project.id, Project.org_id)
            .join(Membership, Membership.org_id == Project.org_id)
            .where(Membership.user_id == user_id)
            .order_by(Project.id)
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
    return {"projectId": row.id, "orgId": row.org_id} if row else None


async def get_membership_role(user_id: int, org_id: int) -> Optional[str]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(Membership.role).where(Membership.user_id == user_id, Membership.org_id == org_id)
        return (await session.execute(stmt)).scalar_one_or_none()


# ── API keys ─────────────────────────────────────────────────────────────
#
# Format tc_<env>_<32 random hex chars> — the "tc_live_"/"tc_test_" prefix
# is what api_keys.environment-adjacent detection is based on downstream
# (nothing today branches on it, but it's the plan's documented format and
# makes a leaked key immediately recognizable in logs/scans as a TrustChain
# credential, same idea as Stripe's sk_live_/sk_test_ prefixes).

def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(environment: str) -> str:
    assert environment in ("live", "test"), f"unknown environment {environment!r}"
    return f"tc_{environment}_{secrets.token_hex(16)}"


async def create_api_key(project_id: int, scopes: list[str], created_at: int, environment: str = "live") -> dict:
    """Returns {"rawKey": ..., "id": ..., "lastFour": ..., "scopes": [...]}
    — rawKey is shown exactly once, here, at creation. It is never stored
    (only its SHA-256 hash is) and never logged."""
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise ValueError(f"unknown scope(s): {sorted(invalid)}")

    raw_key = generate_api_key(environment)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        key_row = ApiKey(
            project_id=project_id, key_hash=_hash_key(raw_key), last_four=raw_key[-4:],
            scopes=scopes, created_at=created_at,
        )
        session.add(key_row)
        await session.commit()
        key_id = key_row.id

    return {"rawKey": raw_key, "id": key_id, "lastFour": raw_key[-4:], "scopes": scopes}


async def verify_api_key(raw_key: str, now: int) -> Optional[dict]:
    """Looks up an API key by its hash and returns its authority
    (project/org/scopes) if it's live — None if it doesn't exist, is
    revoked, or has expired. Also bumps last_used_at (best-effort — a lost
    update here is a staleness issue, not a security one, so it doesn't
    need its own transaction retry logic)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(ApiKey.id, ApiKey.project_id, ApiKey.scopes, ApiKey.revoked_at, ApiKey.expires_at, Project.org_id)
            .join(Project, Project.id == ApiKey.project_id)
            .where(ApiKey.key_hash == _hash_key(raw_key))
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at <= now:
            return None

        await session.execute(update(ApiKey).where(ApiKey.id == row.id).values(last_used_at=now))
        await session.commit()

    return {"id": row.id, "projectId": row.project_id, "orgId": row.org_id, "scopes": row.scopes}


async def list_api_keys(project_id: int) -> list[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id, "lastFour": r.last_four, "scopes": r.scopes, "createdAt": r.created_at,
            "expiresAt": r.expires_at, "revokedAt": r.revoked_at, "lastUsedAt": r.last_used_at,
        }
        for r in rows
    ]


async def revoke_api_key(key_id: int, project_id: int, now: int) -> bool:
    """Scoped by project_id, not just key_id — a key ID is a small
    sequential integer, so without this a caller could revoke another
    project's key just by guessing IDs. Returns False if no matching,
    not-already-revoked key was found (caller maps that to 404)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id)
        key_row = (await session.execute(stmt)).scalar_one_or_none()
        if key_row is None or key_row.revoked_at is not None:
            return False
        key_row.revoked_at = now
        await session.commit()
    return True


# ── Quotas ───────────────────────────────────────────────────────────────

async def count_org_runs_in_window(org_id: int, since: int) -> int:
    """Rolling-window run count across every project in an org, for the
    monthly quota check — a simple request count, not gas metering (see
    get_org_gas_budget_status/record_gas_spend below for the real,
    now-wired gas-cost ceiling, plan §14.1's gas_budget_wei/gas_spent_wei
    and §11.4's "hard gas-spend ceiling ... circuit breaker that
    suspends anchoring on breach")."""
    from db.models import Run

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(func.count(Run.run_id))
            .join(Project, Project.id == Run.project_id)
            .where(Project.org_id == org_id, Run.created_at >= since)
        )
        return (await session.execute(stmt)).scalar_one()


async def get_org_id_for_run(run_id: str) -> Optional[int]:
    """Resolves a run's owning org — anchor_worker/main.py uses this to
    know which org's gas budget a batch's spend should count against and
    be checked before submitting, without needing project_id/org_id
    threaded through claim_batch/build_batches (whose whole shape is
    steps/leaves, not tenancy)."""
    from db.models import Run

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(Project.org_id)
            .join(Run, Run.project_id == Project.id)
            .where(Run.run_id == run_id)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def get_org_gas_budget_status(org_id: int) -> dict:
    """Returns {"gasBudgetWei": int | None, "gasSpentWei": int,
    "breached": bool}. `breached` is always False when gasBudgetWei is
    None — a org with no configured ceiling has nothing to breach, which
    is the deliberate default (existing orgs, and any new one until an
    operator sets a real budget, must not suddenly stop anchoring)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
        if org is None:
            return {"gasBudgetWei": None, "gasSpentWei": 0, "breached": False}
        breached = org.gas_budget_wei is not None and org.gas_spent_wei >= org.gas_budget_wei
        return {"gasBudgetWei": org.gas_budget_wei, "gasSpentWei": org.gas_spent_wei, "breached": breached}


async def record_gas_spend(org_id: int, wei_spent: int) -> None:
    """Called once per CONFIRMED anchor batch (anchor_worker/submit.py),
    with that batch's real receipt.gasUsed * effectiveGasPrice — the
    actual cost paid, not an estimate. A single UPDATE ... SET x = x +
    :delta rather than read-then-write: correct under concurrent batch
    confirmations (this worker is the sole nonce authority for
    SUBMITTING — see nonce_lock.py — but multiple batches can still be
    IN FLIGHT/confirming across overlapping polls) without needing a
    row lock here."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(gas_spent_wei=Organization.gas_spent_wei + wei_spent)
        )
        await session.commit()


async def get_org_token_budget_status(org_id: int) -> dict:
    """Returns {"tokenBudget": int | None, "tokensSpent": int, "breached":
    bool} — same shape/defaults as get_org_gas_budget_status above:
    `breached` is always False when tokenBudget is None (no configured
    ceiling means nothing to breach)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
        if org is None:
            return {"tokenBudget": None, "tokensSpent": 0, "breached": False}
        breached = org.token_budget is not None and org.tokens_spent >= org.token_budget
        return {"tokenBudget": org.token_budget, "tokensSpent": org.tokens_spent, "breached": breached}


async def record_token_spend(org_id: int, tokens: int) -> None:
    """Called once per completed pipeline run (main.py's
    _run_pipeline_background, on the run_complete event), with that run's
    real cumulative usage_metadata total across all 5 LLM calls — not an
    estimate. Atomic UPDATE ... SET x = x + :delta, same reasoning as
    record_gas_spend above (correct under concurrent runs completing for
    the same org without a row lock)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(tokens_spent=Organization.tokens_spent + tokens)
        )
        await session.commit()
