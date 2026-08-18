"""
tests/test_memberships.py — role changes, removal, and ownership
transfer over the real HTTP surface (Phase 3 §5.5/§9.4). Each test
builds a real multi-member org via the invitation flow (not a direct DB
insert) so it exercises the same path a real team actually goes through.
"""

import asyncio

import db
from db.tenancy import join_org_via_invitation
from tests.conftest import seed_user_and_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


def _add_member(org_id: int, email: str, role: str, invited_by: int) -> dict:
    """Adds a real second (or third) member to org_id without going
    through the full email-invitation round trip — join_org_via_invitation
    is the exact function POST /auth/signup's invite_token path and
    POST /invitations/{token}/accept both call, so this is real
    membership-creation logic, just skipping the token/email plumbing
    test_invitations.py exercises directly."""
    from db.engine import get_sessionmaker

    user = _run(db.create_user(email=email, name=email.split("@")[0], password="testpassword123", created_at=1_700_000_100))

    async def _join():
        async with get_sessionmaker()() as session:
            await join_org_via_invitation(session, user["userId"], org_id, role, invited_by, 1_700_000_200)
            await session.commit()

    _run(_join())

    import auth
    token = auth.create_token(email=user["email"], name=user["name"], project_id=user["projectId"], org_id=org_id, user_id=user["userId"])
    return {**user, "token": token, "orgId": org_id}


def test_admin_can_promote_viewer_to_member(client):
    owner = seed_user_and_token(email="mem_owner1@example.com")
    viewer = _add_member(owner["orgId"], "mem_viewer1@example.com", "viewer", owner["userId"])

    r = client.patch(
        f"/orgs/{owner['orgId']}/members/{viewer['userId']}", json={"role": "member"}, headers=_auth(owner["token"]),
    )
    assert r.status_code == 200

    members = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(owner["token"])).json()["members"]
    roles = {m["userId"]: m["role"] for m in members}
    assert roles[viewer["userId"]] == "member"


def test_admin_cannot_create_another_admin(client):
    owner = seed_user_and_token(email="mem_owner2@example.com")
    admin = _add_member(owner["orgId"], "mem_admin2@example.com", "admin", owner["userId"])
    viewer = _add_member(owner["orgId"], "mem_viewer2@example.com", "viewer", owner["userId"])

    r = client.patch(
        f"/orgs/{owner['orgId']}/members/{viewer['userId']}", json={"role": "admin"}, headers=_auth(admin["token"]),
    )
    assert r.status_code == 403
    assert r.json()["error_code"] == "insufficient_role"


def test_admin_cannot_touch_an_owner(client):
    owner = seed_user_and_token(email="mem_owner3@example.com")
    admin = _add_member(owner["orgId"], "mem_admin3@example.com", "admin", owner["userId"])

    r = client.patch(
        f"/orgs/{owner['orgId']}/members/{owner['userId']}", json={"role": "member"}, headers=_auth(admin["token"]),
    )
    assert r.status_code == 403


def test_cannot_modify_your_own_role(client):
    owner = seed_user_and_token(email="mem_owner4@example.com")
    admin = _add_member(owner["orgId"], "mem_admin4@example.com", "admin", owner["userId"])

    r = client.patch(
        f"/orgs/{owner['orgId']}/members/{admin['userId']}", json={"role": "viewer"}, headers=_auth(admin["token"]),
    )
    assert r.status_code == 400
    assert r.json()["error_code"] == "cannot_modify_own_role"


def test_invalid_role_rejected(client):
    owner = seed_user_and_token(email="mem_owner5@example.com")
    viewer = _add_member(owner["orgId"], "mem_viewer5@example.com", "viewer", owner["userId"])

    r = client.patch(
        f"/orgs/{owner['orgId']}/members/{viewer['userId']}", json={"role": "superadmin"}, headers=_auth(owner["token"]),
    )
    assert r.status_code == 400
    assert r.json()["error_code"] == "invalid_role"

    # 'owner' specifically is rejected too — ownership is transferred, not
    # granted via a role change (ADR-0014).
    r2 = client.patch(
        f"/orgs/{owner['orgId']}/members/{viewer['userId']}", json={"role": "owner"}, headers=_auth(owner["token"]),
    )
    assert r2.status_code == 400


def test_cannot_remove_the_last_owner(client):
    owner = seed_user_and_token(email="mem_owner6@example.com")
    r = client.delete(f"/orgs/{owner['orgId']}/members/{owner['userId']}", headers=_auth(owner["token"]))
    assert r.status_code == 403  # rank check: owner cannot remove someone at/above their own rank, including themselves via this path

    r2 = client.delete(f"/orgs/{owner['orgId']}/members/me", headers=_auth(owner["token"]))
    assert r2.status_code == 400
    assert r2.json()["error_code"] == "cannot_remove_last_owner"


def test_remove_member(client):
    owner = seed_user_and_token(email="mem_owner7@example.com")
    member = _add_member(owner["orgId"], "mem_member7@example.com", "member", owner["userId"])

    r = client.delete(f"/orgs/{owner['orgId']}/members/{member['userId']}", headers=_auth(owner["token"]))
    assert r.status_code == 200

    members = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(owner["token"])).json()["members"]
    assert member["userId"] not in {m["userId"] for m in members}


def test_member_can_leave_voluntarily(client):
    owner = seed_user_and_token(email="mem_owner8@example.com")
    member = _add_member(owner["orgId"], "mem_member8@example.com", "member", owner["userId"])

    r = client.delete(f"/orgs/{owner['orgId']}/members/me", headers=_auth(member["token"]))
    assert r.status_code == 200

    members = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(owner["token"])).json()["members"]
    assert member["userId"] not in {m["userId"] for m in members}


def test_transfer_ownership_is_atomic(client):
    owner = seed_user_and_token(email="mem_owner9@example.com")
    admin = _add_member(owner["orgId"], "mem_admin9@example.com", "admin", owner["userId"])

    r = client.post(
        f"/orgs/{owner['orgId']}/transfer-ownership", json={"user_id": admin["userId"]}, headers=_auth(owner["token"]),
    )
    assert r.status_code == 200

    members = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(admin["token"])).json()["members"]
    roles = {m["userId"]: m["role"] for m in members}
    assert roles[admin["userId"]] == "owner"
    assert roles[owner["userId"]] == "admin"

    # A critical alert was raised for the transfer.
    alerts = client.get("/alerts?severity=critical", headers=_auth(admin["token"])).json()["alerts"]
    assert any(a["alertType"] == "ownership_transferred" for a in alerts)


def test_removed_member_cannot_manage_the_org_anymore(client):
    """Not a token-liveness test (see test_membership_revocation.py for
    that) — this just confirms the HTTP-level consequence: a removed
    member's EXISTING role no longer grants anything once the membership
    itself is gone and the cache is invalidated on the same request."""
    owner = seed_user_and_token(email="mem_owner10@example.com")
    admin = _add_member(owner["orgId"], "mem_admin10@example.com", "admin", owner["userId"])
    client.delete(f"/orgs/{owner['orgId']}/members/{admin['userId']}", headers=_auth(owner["token"]))

    r = client.get(f"/orgs/{owner['orgId']}/members", headers=_auth(admin["token"]))
    assert r.status_code in (401, 403)
