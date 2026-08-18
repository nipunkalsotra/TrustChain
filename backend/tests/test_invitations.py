"""
tests/test_invitations.py — the full invitation lifecycle (Phase 3 §5),
over the real HTTP surface plus direct db/invitations.py calls for the
concurrency test (which needs to race two calls against the exact same
transaction window, not just two sequential HTTP requests).

The single most important property tested here (Phase 3 plan's own
stated success criterion, §3.3): a NEW user signing up through a valid
invite_token joins the INVITER's org and does NOT also receive a
personal org of their own.
"""

import asyncio
import time

import db
import db.invitations as invitations_db
from tests.conftest import seed_user_and_token


def _now() -> int:
    """Real wall-clock time, not an arbitrary fixed epoch — the
    unauthenticated preview/signup/accept paths all check expiry against
    REAL int(time.time()), not whatever `now` create_invitation was
    called with. A stale hardcoded epoch (e.g. 1_700_000_000, which is
    already in the past relative to the actual current date) makes an
    invitation appear expired the moment anything reads it back through
    one of those paths — a real bug this file's own first test run
    caught."""
    return int(time.time())


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


def test_creating_an_invitation_never_returns_the_raw_token(client):
    """Only a hash is ever stored (db/invitations.py::_hash_token,
    mirroring ApiKey.key_hash) — the create endpoint's own response must
    not leak it either, since the raw token exists exactly once, in the
    email."""
    owner = seed_user_and_token(email="inv_owner1@example.com")
    r = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "inv_newbie1@example.com", "role": "member"},
        headers=_auth(owner["token"]),
    )
    assert r.status_code == 200
    assert "rawToken" not in r.json() and "token" not in r.json()


def test_invite_then_signup_end_to_end(client):
    owner = seed_user_and_token(email="inv_owner2@example.com")

    # Create the invitation directly through db/invitations.py so this
    # test has the real raw token in hand — the same function
    # POST /orgs/{id}/invitations calls internally.
    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_newbie2@example.com", "member", owner["userId"], _now(), 604800,
    ))
    raw_token = result["rawToken"]

    # Preview works unauthenticated.
    preview = client.get(f"/invitations/{raw_token}")
    assert preview.status_code == 200
    assert preview.json()["role"] == "member"

    # Sign up through it.
    signup = client.post("/auth/signup", json={
        "name": "New Member", "email": "inv_newbie2@example.com", "password": "testpassword123",
        "invite_token": raw_token,
    })
    assert signup.status_code == 200
    new_token = signup.json()["token"]

    # Joined the INVITER's org, not a personal one.
    me = client.get("/me", headers=_auth(new_token)).json()
    assert len(me["memberships"]) == 1
    assert me["memberships"][0]["org"]["id"] == owner["orgId"]
    assert me["memberships"][0]["role"] == "member"
    assert me["memberships"][0]["org"]["name"] != "New Member's Organization"

    # The invitation is now accepted, not preview-able again.
    preview2 = client.get(f"/invitations/{raw_token}")
    assert preview2.status_code == 404


def test_existing_user_accepts_invitation(client):
    owner = seed_user_and_token(email="inv_owner3@example.com")
    existing = seed_user_and_token(email="inv_existing3@example.com")

    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_existing3@example.com", "admin", owner["userId"], _now(), 604800,
    ))

    r = client.post(f"/invitations/{result['rawToken']}/accept", headers=_auth(existing["token"]))
    assert r.status_code == 200
    assert r.json()["membership"]["role"] == "admin"

    me = client.get("/me", headers=_auth(r.json()["token"])).json()
    org_ids = {m["org"]["id"] for m in me["memberships"]}
    assert owner["orgId"] in org_ids  # joined IN ADDITION to their own personal org
    assert existing["orgId"] in org_ids


def test_invitation_email_mismatch_rejected_at_signup(client):
    owner = seed_user_and_token(email="inv_owner4@example.com")
    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_intended4@example.com", "member", owner["userId"], _now(), 604800,
    ))

    r = client.post("/auth/signup", json={
        "name": "Wrong Person", "email": "inv_wrong4@example.com", "password": "testpassword123",
        "invite_token": result["rawToken"],
    })
    assert r.status_code == 400
    assert r.json()["error_code"] == "invitation_email_mismatch"


def test_expired_invitation_rejected(client):
    owner = seed_user_and_token(email="inv_owner5@example.com")
    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_expired5@example.com", "member", owner["userId"], 1_000_000_000, 1,  # ttl=1s, created far in the past
    ))

    r = client.get(f"/invitations/{result['rawToken']}")
    assert r.status_code == 404


def test_revoked_invitation_rejected(client):
    owner = seed_user_and_token(email="inv_owner6@example.com")
    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_revoked6@example.com", "member", owner["userId"], _now(), 604800,
    ))
    invitation_id = _run(invitations_db.list_invitations(owner["orgId"], status="pending"))[0]["id"]

    r = client.delete(f"/orgs/{owner['orgId']}/invitations/{invitation_id}", headers=_auth(owner["token"]))
    assert r.status_code == 200

    preview = client.get(f"/invitations/{result['rawToken']}")
    assert preview.status_code == 404


def test_duplicate_pending_invitation_rejected(client):
    owner = seed_user_and_token(email="inv_owner7@example.com")
    r1 = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "inv_dupe7@example.com", "role": "member"},
        headers=_auth(owner["token"]),
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "inv_dupe7@example.com", "role": "admin"},
        headers=_auth(owner["token"]),
    )
    assert r2.status_code == 409


def test_single_use_under_concurrency(client):
    """The actual single-use guarantee (Phase 3 §5.1/ADR-0014): two
    'simultaneous' accept attempts for the SAME token — only one may
    win. Not true OS-level concurrency (asyncio.gather over two DB
    sessions is enough to exercise the conditional UPDATE's real
    behavior, since both attempts genuinely race for the same row)."""
    owner = seed_user_and_token(email="inv_owner8@example.com")
    result = _run(invitations_db.create_invitation(
        owner["orgId"], "inv_race8@example.com", "member", owner["userId"], 1_700_000_000, 604800,
    ))
    user_a = _run(db.create_user(email="inv_race8@example.com", name="Racer", password="testpassword123", created_at=1_700_000_100))

    async def _attempt():
        try:
            return await invitations_db.accept_for_existing_user(result["rawToken"], user_a["userId"], "inv_race8@example.com", 1_700_000_200)
        except Exception as e:
            return e

    async def _race():
        return await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)

    outcomes = _run(_race())
    successes = [o for o in outcomes if isinstance(o, dict)]
    failures = [o for o in outcomes if not isinstance(o, dict)]
    assert len(successes) == 1
    assert len(failures) == 1


def test_max_pending_invitations_ceiling(client, monkeypatch):
    from config import get_settings
    monkeypatch.setenv("MAX_PENDING_INVITATIONS_PER_ORG", "2")
    get_settings.cache_clear()

    owner = seed_user_and_token(email="inv_owner9@example.com")
    for i in range(2):
        r = client.post(
            f"/orgs/{owner['orgId']}/invitations", json={"email": f"inv_ceiling9_{i}@example.com", "role": "member"},
            headers=_auth(owner["token"]),
        )
        assert r.status_code == 200

    r_over = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "inv_ceiling9_over@example.com", "role": "member"},
        headers=_auth(owner["token"]),
    )
    assert r_over.status_code == 400
    assert r_over.json()["error_code"] == "too_many_pending_invitations"

    get_settings.cache_clear()
