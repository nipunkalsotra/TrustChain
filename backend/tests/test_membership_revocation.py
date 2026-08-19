"""
tests/test_membership_revocation.py — the JWT membership-liveness check
(ADR-0019/Phase 3 §4.3). The property under test: a 7-day session JWT is
self-contained and never re-resolved against the DB by design (see
auth.py's module docstring) — this is what stops an already-issued token
from outliving a later removal or role demotion anyway.
"""

import asyncio

import membership_cache
from db.tenancy import join_org_via_invitation
from tests.conftest import seed_user_and_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


def _add_admin(org_id: int, email: str, invited_by: int) -> dict:
    import auth
    import db
    from db.engine import get_sessionmaker

    user = _run(db.create_user(email=email, name=email.split("@")[0], password="testpassword123", created_at=1_700_000_100))

    async def _join():
        async with get_sessionmaker()() as session:
            await join_org_via_invitation(session, user["userId"], org_id, "admin", invited_by, 1_700_000_200)
            await session.commit()

    _run(_join())
    token = auth.create_token(email=user["email"], name=user["name"], project_id=user["projectId"], org_id=org_id, user_id=user["userId"])
    return {**user, "token": token, "orgId": org_id}


def test_removed_members_existing_token_stops_working_immediately(client):
    """The core claim: revocation is effectively INSTANT (cache
    invalidation on the same request that removes the member), not
    bounded by the cache TTL — this test would still pass with just the
    TTL as a fallback, but asserts the immediate path specifically by
    checking access fails on the VERY NEXT request, not "eventually"."""
    owner = seed_user_and_token(email="revoke_owner1@example.com")
    admin = _add_admin(owner["orgId"], "revoke_admin1@example.com", owner["userId"])

    # Confirm the token works before removal.
    r_before = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(admin["token"]))
    assert r_before.status_code == 200

    client.delete(f"/orgs/{owner['orgId']}/members/{admin['userId']}", headers=_auth(owner["token"]))

    # Same token, immediately after removal — no sleep, no TTL wait.
    r_after = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(admin["token"]))
    assert r_after.status_code in (401, 403)
    if r_after.status_code == 401:
        assert r_after.json()["error_code"] == "membership_revoked"


def test_role_downgrade_is_reflected_on_the_next_request(client):
    owner = seed_user_and_token(email="revoke_owner2@example.com")
    admin = _add_admin(owner["orgId"], "revoke_admin2@example.com", owner["userId"])

    r_before = client.get(f"/orgs/{owner['orgId']}/audit-events", headers=_auth(admin["token"]))
    assert r_before.status_code == 200  # admin can read audit events

    client.patch(f"/orgs/{owner['orgId']}/members/{admin['userId']}", json={"role": "viewer"}, headers=_auth(owner["token"]))

    r_after = client.get(f"/orgs/{owner['orgId']}/audit-events", headers=_auth(admin["token"]))
    assert r_after.status_code == 403  # viewer cannot read audit events (admin+ only)


def test_membership_cache_invalidation_actually_clears_the_key(client):
    """Lower-level than the HTTP tests above: directly asserts
    membership_cache.get_role_cached returns the NEW state right after
    invalidate(), without relying on TTL expiry."""
    owner = seed_user_and_token(email="revoke_owner3@example.com")
    admin = _add_admin(owner["orgId"], "revoke_admin3@example.com", owner["userId"])

    role = _run(membership_cache.get_role_cached(admin["userId"], owner["orgId"]))
    assert role == "admin"

    client.delete(f"/orgs/{owner['orgId']}/members/{admin['userId']}", headers=_auth(owner["token"]))

    # The endpoint's own request already called invalidate() — re-reading
    # here must see the cleared state, not a stale cached "admin".
    role_after = _run(membership_cache.get_role_cached(admin["userId"], owner["orgId"]))
    assert role_after is None


def test_membership_check_fails_closed_through_to_postgres_when_redis_is_down(monkeypatch):
    """ADR-0019's stated fail-closed guarantee: an unreachable Redis must
    NOT be treated as 'still authorized' — get_role_cached should fall
    through to a real Postgres read and return the true current state
    either way."""
    owner = seed_user_and_token(email="revoke_owner4@example.com")
    admin = _add_admin(owner["orgId"], "revoke_admin4@example.com", owner["userId"])

    class _BrokenRedis:
        async def get(self, *a, **kw):
            raise ConnectionError("redis is down")

        async def set(self, *a, **kw):
            raise ConnectionError("redis is down")

        async def delete(self, *a, **kw):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(membership_cache, "get_redis", lambda: _BrokenRedis())

    # Still a real member — the Postgres fallback must say so.
    role = _run(membership_cache.get_role_cached(admin["userId"], owner["orgId"]))
    assert role == "admin"

    # Directly remove the membership at the DB layer (bypassing the
    # endpoint, since that ALSO calls invalidate() which would itself hit
    # the broken Redis — this isolates the fallback-read path itself).
    import db.orgs as orgs_db
    _run(orgs_db.remove_member(admin["userId"], owner["orgId"]))

    role_after = _run(membership_cache.get_role_cached(admin["userId"], owner["orgId"]))
    assert role_after is None
