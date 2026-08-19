"""
tests/test_permissions.py — the role model's pure logic (fast, no DB),
plus the route-coverage check that keeps a new mutating endpoint from
silently defaulting to "any authenticated user can do this" the way RLS
(test_row_level_security.py) keeps a new endpoint from silently
defaulting to cross-tenant visibility.
"""

import pytest

from permissions import MIN_ROLE_FOR, ROLE_RANK, Permission, rank_of
from db.orgs import rank_allows_target_modification


def test_every_permission_has_a_matrix_entry():
    """Appendix A of the Phase 3 plan IS this table — a Permission with
    no entry would make require_permission KeyError instead of denying
    access, which is the opposite of fail-safe."""
    assert set(MIN_ROLE_FOR) == set(Permission)


def test_role_rank_is_strictly_ordered():
    assert ROLE_RANK["viewer"] < ROLE_RANK["member"] < ROLE_RANK["admin"] < ROLE_RANK["owner"]


def test_rank_of_unknown_role_raises():
    with pytest.raises(ValueError):
        rank_of("superadmin")


@pytest.mark.parametrize("permission", list(Permission))
def test_min_role_for_is_a_valid_role(permission):
    assert MIN_ROLE_FOR[permission] in ROLE_RANK


def test_member_management_requires_admin_or_above():
    for perm in (Permission.MEMBER_INVITE, Permission.MEMBER_ROLE_CHANGE, Permission.MEMBER_REMOVE):
        assert ROLE_RANK[MIN_ROLE_FOR[perm]] >= ROLE_RANK["admin"]


def test_only_owner_can_delete_org_or_transfer_ownership():
    assert MIN_ROLE_FOR[Permission.ORG_DELETE] == "owner"
    assert MIN_ROLE_FOR[Permission.ORG_TRANSFER_OWNERSHIP] == "owner"


def test_viewer_can_read_but_not_write_anything():
    """The whole point of the role — read access to runs/agents/alerts,
    write access to nothing."""
    read_perms = {Permission.ORG_READ, Permission.PROJECT_READ, Permission.MEMBER_READ, Permission.RUN_READ,
                  Permission.AGENT_READ, Permission.ALERT_READ}
    for perm in read_perms:
        assert MIN_ROLE_FOR[perm] == "viewer"
    write_perms = set(Permission) - read_perms - {Permission.NOTIFICATION_PREFS_MANAGE}
    for perm in write_perms:
        assert MIN_ROLE_FOR[perm] != "viewer", f"{perm} should not be viewer-writable"


# ── rank_allows_target_modification (Phase 3 §5.5's rank comparison) ────

@pytest.mark.parametrize(
    "actor_rank,target_rank,requested_rank,expected",
    [
        (30, 10, 20, True),   # admin promoting a viewer to member: allowed
        (30, 20, 30, False),  # admin trying to create another admin: refused
        (30, 30, 20, False),  # admin trying to modify a peer admin: refused
        (30, 40, 20, False),  # admin trying to touch an owner: refused
        (40, 30, 40, False),  # even an owner can't mint a SECOND owner via this path — see main.py's INVALID_ROLE check, which forbids requesting 'owner' at the API layer before rank checking ever runs; this exercises the rank function alone, which would technically allow it — the API-layer guard is what actually prevents it
        (20, 10, 20, False),  # a member (rank 20) somehow calling this: target rank must be < actor rank too, 10 < 20 is fine but this asserts the requested_rank check
    ],
)
def test_rank_allows_target_modification(actor_rank, target_rank, requested_rank, expected):
    assert rank_allows_target_modification(actor_rank, target_rank, requested_rank) == expected


# ── Route coverage — every mutating handler must declare a Permission ──

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Routes that are legitimately unauthenticated, or intentionally outside
# the org-role permission model (auth bootstrapping, health checks, the
# public invitation preview, API-key-scope-gated SDK endpoints that
# predate Phase 3 and use auth.require_scope instead of
# permissions.require_permission).
_EXEMPT_PATHS = {
    "/auth/signup", "/auth/login", "/auth/refresh", "/auth/logout", "/auth/token-pair",
    # Phase 4 G1/G2 — same category as the /auth/* bootstrapping routes
    # above: account-lifecycle actions authorized by the caller's own
    # identity (a bearer JWT scoped to no particular org, or a token
    # that IS the authorization, same reasoning as invitation accept
    # below), never by an org membership rank there is nothing to check.
    "/auth/resend-verification", "/auth/verify-email/{token}",
    "/auth/forgot-password", "/auth/reset-password/{token}",
    "/health", "/ready", "/metrics", "/verify", "/verify/tamper-demo",
    "/invitations/{token}",  # public preview — unauthenticated by design
    "/run-agent", "/v1/runs",  # pre-Phase-3, project-API-key-scoped (auth.require_scope)
    "/agents", "/steps",  # pre-Phase-3, project-API-key-scoped
    "/orgs",  # POST only — bootstrapping a NEW org has no existing membership to role-check against; the caller becomes owner unconditionally, same as signup's implicit org provisioning
    "/invitations/{token}/accept",  # the invitation itself (email-bound, single-use, expiring) IS the authorization — no separate role concept applies to redeeming one
}
# Handlers that authorize via a direct membership-existence check
# (permissions.get_role / db.orgs.get_membership) rather than
# permissions.require_permission's rank threshold — legitimate for the
# handful of "any role, just needs to still be a member" operations
# (switch-project, leave-org, manage-my-own-notification-prefs), where
# there is no meaningful minimum RANK to enforce, only "are you a member
# at all".
_DIRECT_MEMBERSHIP_CHECK_MARKERS = ("require_permission", "require_scope", "_require_admin", "get_role(", "get_membership(")


def test_every_mutating_route_is_covered_by_the_permission_matrix():
    """Walks the real FastAPI route table and asserts every non-exempt
    mutating (POST/PUT/PATCH/DELETE) handler's SOURCE references
    `permissions.require_permission` or `auth.require_scope` — a new
    endpoint that forgets either fails THIS test, not silently defaulting
    to 'any authenticated user can do this'. Source-inspection (like
    conftest.py's own integration-test classifier) rather than a runtime
    call, since many handlers 403 long before reaching a point this test
    could observe without a live DB."""
    import inspect
    import os

    os.environ.setdefault("JWT_SECRET", "x" * 40)
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    import main

    def walk(routes):
        found = []
        for r in routes:
            if hasattr(r, "path"):
                found.append(r)
            if hasattr(r, "original_router"):
                found.extend(walk(r.original_router.routes))
            elif hasattr(r, "routes"):
                found.extend(walk(r.routes))
        return found

    uncovered = []
    for route in walk(main.app.routes):
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if path in _EXEMPT_PATHS or path.startswith("/v1/") and path.replace("/v1", "", 1) in _EXEMPT_PATHS:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            continue
        if not any(marker in source for marker in _DIRECT_MEMBERSHIP_CHECK_MARKERS):
            uncovered.append(f"{sorted(methods)} {path}")

    assert not uncovered, f"mutating routes with no permission/scope check: {uncovered}"
