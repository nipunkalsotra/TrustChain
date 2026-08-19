"""
permissions.py — the org-level role model and its one enforcement point.

Phase 2 had exactly one role check in the whole codebase: main.py's
_require_admin(), inlined as `role not in ("owner", "admin")`, gating only
API-key create/list/revoke. Phase 3 gives every membership-bearing
organization real people with real roles doing real things to it — invite
members, change roles, create projects, acknowledge alerts — so "one
literal role check, copy-pasted as needed" stops being viable: it is
exactly the shape of gap invariant I7 predicts (RLS exists because
"a future endpoint forgets a WHERE clause" is not hypothetical, see
ADR-0006) applied to authorization instead of tenancy.

This module is the single source of truth instead. ROLE_RANK gives the
four roles a total order so "can X modify Y" is one comparison instead of
a hand-rolled `if role == "owner" or (role == "admin" and target != ...)`
at every call site. MIN_ROLE_FOR maps every mutating (and a few sensitive
read) operation to the minimum rank required — Appendix A of the Phase 3
plan renders this exact table for the frontend, so the table here IS the
contract, not documentation of one.

require_permission() is the only function main.py's route handlers call.
tests/test_permissions.py walks FastAPI's own route table and asserts
every mutating handler declares a Permission — a new endpoint that forgets
to costs a failed test, not a silent "any authenticated user can do this"
regression, the same protection RLS gives one layer down.
"""

from enum import Enum
from typing import Optional

from sqlalchemy import select

from db.engine import get_sessionmaker
from db.models import Membership
from errors import ApiError, ErrorCode

# Higher rank strictly dominates lower rank. Integers (not just an ordered
# enum) so "is the target's rank below the actor's rank" is a plain `<`
# comparison, used repeatedly by the rank-limited member operations below.
ROLE_RANK: dict[str, int] = {"viewer": 10, "member": 20, "admin": 30, "owner": 40}

# The four roles a membership row can ever hold — also enforced as a
# Postgres CHECK constraint (migration d1e2f3a4b5c6) so a bug that writes
# a garbage role value fails loudly at the database, not silently here.
VALID_ROLES = frozenset(ROLE_RANK)


class Permission(str, Enum):
    ORG_READ = "org:read"
    ORG_UPDATE = "org:update"
    ORG_DELETE = "org:delete"
    ORG_TRANSFER_OWNERSHIP = "org:transfer_ownership"

    PROJECT_READ = "project:read"
    PROJECT_CREATE = "project:create"
    PROJECT_UPDATE = "project:update"
    PROJECT_DELETE = "project:delete"

    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_ROLE_CHANGE = "member:role_change"
    MEMBER_REMOVE = "member:remove"

    APIKEY_READ = "apikey:read"
    APIKEY_CREATE = "apikey:create"
    APIKEY_REVOKE = "apikey:revoke"

    RUN_READ = "run:read"
    RUN_CREATE = "run:create"

    AGENT_READ = "agent:read"
    AGENT_REGISTER = "agent:register"
    AGENT_REVOKE = "agent:revoke"

    STEP_WRITE = "step:write"

    ALERT_READ = "alert:read"
    ALERT_ACKNOWLEDGE = "alert:acknowledge"
    ALERT_RESOLVE = "alert:resolve"

    AUDIT_READ = "audit:read"

    NOTIFICATION_PREFS_MANAGE = "notification_prefs:manage"


# The matrix. Appendix A of the Phase 3 plan is this table, rendered.
MIN_ROLE_FOR: dict[Permission, str] = {
    Permission.ORG_READ: "viewer",
    Permission.ORG_UPDATE: "admin",
    Permission.ORG_DELETE: "owner",
    Permission.ORG_TRANSFER_OWNERSHIP: "owner",
    Permission.PROJECT_READ: "viewer",
    Permission.PROJECT_CREATE: "admin",
    Permission.PROJECT_UPDATE: "admin",
    Permission.PROJECT_DELETE: "owner",
    Permission.MEMBER_READ: "viewer",
    Permission.MEMBER_INVITE: "admin",
    Permission.MEMBER_ROLE_CHANGE: "admin",
    Permission.MEMBER_REMOVE: "admin",
    Permission.APIKEY_READ: "admin",
    Permission.APIKEY_CREATE: "admin",
    Permission.APIKEY_REVOKE: "admin",
    Permission.RUN_READ: "viewer",
    Permission.RUN_CREATE: "member",
    Permission.AGENT_READ: "viewer",
    Permission.AGENT_REGISTER: "member",
    Permission.AGENT_REVOKE: "admin",
    Permission.STEP_WRITE: "member",
    Permission.ALERT_READ: "viewer",
    Permission.ALERT_ACKNOWLEDGE: "admin",
    Permission.ALERT_RESOLVE: "admin",
    Permission.AUDIT_READ: "admin",
    Permission.NOTIFICATION_PREFS_MANAGE: "viewer",  # own preferences only — callers must additionally scope by user_id
}

assert set(MIN_ROLE_FOR) == set(Permission), "every Permission must have a MIN_ROLE_FOR entry"

# Phase 4 G1: an unverified account's email address might not actually
# belong to the person who registered it — every guarantee this system
# makes about "the owner gets emailed" assumes it does. Rather than
# scatter `if not current_user.email_verified` checks across the
# specific endpoints that matter (member invites, API key creation — the
# two ways an unverified account could hand out real authority or
# credentials before that assumption holds), the check lives here,
# alongside the role check every one of those endpoints already goes
# through require_permission for. A permission NOT in this set is
# unaffected — read access, for instance, never required a verified
# email and still doesn't.
REQUIRES_VERIFIED_EMAIL: frozenset[Permission] = frozenset({
    Permission.MEMBER_INVITE,
    Permission.APIKEY_CREATE,
})


async def get_role(user_id: int, org_id: int) -> Optional[str]:
    """Bare membership-role lookup, no error handling — used both by
    require_permission below and by auth.py's membership liveness check
    (Phase 3 §4.3), which needs the same query but must swallow "not a
    member" into 401 MEMBERSHIP_REVOKED rather than 403 INSUFFICIENT_ROLE."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(Membership.role).where(Membership.user_id == user_id, Membership.org_id == org_id)
        return (await session.execute(stmt)).scalar_one_or_none()


async def require_permission(user_id: int, org_id: int, permission: Permission) -> str:
    """Raises ApiError(403, INSUFFICIENT_ROLE) if the caller's rank in
    org_id is below MIN_ROLE_FOR[permission]; raises the same error if
    they hold no membership in that org at all (a non-member has rank
    -infinity, functionally). For permission in REQUIRES_VERIFIED_EMAIL,
    also raises ApiError(403, EMAIL_NOT_VERIFIED) if the caller's own
    email isn't verified yet — checked AFTER the role check, so a caller
    who additionally lacks the role gets the more fundamental
    INSUFFICIENT_ROLE error, not a confusing EMAIL_NOT_VERIFIED for an
    action they couldn't do anyway. Returns the resolved role so callers
    that need finer-grained logic afterward (e.g. the rank-vs-target
    comparison member-management endpoints do, see main.py's member
    handlers) get it without a second query."""
    role = await get_role(user_id, org_id)
    if role is None or ROLE_RANK.get(role, -1) < ROLE_RANK[MIN_ROLE_FOR[permission]]:
        raise ApiError(403, f"role '{role}' cannot perform '{permission.value}'", ErrorCode.INSUFFICIENT_ROLE)

    if permission in REQUIRES_VERIFIED_EMAIL and not await _is_email_verified(user_id):
        raise ApiError(
            403, f"email address must be verified before '{permission.value}'", ErrorCode.EMAIL_NOT_VERIFIED,
        )
    return role


async def _is_email_verified(user_id: int) -> bool:
    """Thin re-export of db.email_verification.is_verified — deferred
    import to avoid a module-load-time cycle (db/email_verification.py
    doesn't import permissions.py, but db/__init__.py's own import graph
    is broad enough that importing it eagerly at permissions.py's module
    level isn't worth the risk for one function). A fresh DB read, not
    JWT-embedded — verifying an email takes effect immediately for the
    very next request, without requiring the caller to log in again for
    a new token."""
    from db.email_verification import is_verified

    return await is_verified(user_id)


def rank_of(role: str) -> int:
    """ROLE_RANK lookup that fails loudly on an unknown role rather than
    silently treating it as rank 0 — an unrecognized role string reaching
    this function is a bug (the CHECK constraint should have prevented the
    row from existing), not a normal "unprivileged user" case."""
    if role not in ROLE_RANK:
        raise ValueError(f"unknown role: {role!r}")
    return ROLE_RANK[role]
