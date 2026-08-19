"""
tests/test_email_verification.py — email verification (Phase 4 G1), over
the real HTTP surface plus the memory email backend (notifications/
backends/memory.py) to assert on what actually got "sent", the same
pattern tests/test_email_delivery.py uses for the real SMTP/Brevo
backends. Uses a real Postgres (Testcontainers, see conftest.py) — no
mocked database, matching this repo's stated testing philosophy.
"""

import asyncio
import re
import time

import pytest

from tests.conftest import seed_user_and_token

import db.alerts as alerts_db
import db.email_verification as email_verification_db
import db.invitations as invitations_db


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def memory_backend(monkeypatch):
    """Real email-backend swap (env var + settings cache-clear, same
    pattern test_email_delivery.py's real_smtp_server/real_brevo_server
    fixtures use) — not a mock of the send call itself. notifications/
    verification.py's actual get_backend("memory") path runs for real;
    only the transport is captured-in-memory instead of a real socket."""
    from config import get_settings
    from notifications.backends import memory

    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    get_settings.cache_clear()
    memory.reset()

    yield memory

    memory.reset()
    get_settings.cache_clear()


def _extract_token(text_body: str, label: str) -> str:
    m = re.search(rf"{label}: (\S+)", text_body)
    assert m, f"no {label!r} found in email body:\n{text_body}"
    return m.group(1)


def test_signup_queues_a_real_verification_email(client, memory_backend):
    r = client.post("/auth/signup", json={
        "name": "Vera Verify", "email": "verify_signup1@example.com", "password": "verification-test-password-123",
    })
    assert r.status_code == 200

    assert len(memory_backend.SENT) == 1
    sent = memory_backend.SENT[0]
    assert sent["to"] == "verify_signup1@example.com"
    assert "verify" in sent["subject"].lower()
    assert "Verification token:" in sent["textBody"]


def test_new_signup_is_unverified_until_the_link_is_used(client, memory_backend):
    r = client.post("/auth/signup", json={
        "name": "Vera Verify", "email": "verify_flow1@example.com", "password": "verification-test-password-123",
    })
    token = r.json()["token"]

    me = client.get("/me", headers=_auth(token))
    assert me.json()["user"]["emailVerified"] is False

    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Verification token")
    r2 = client.post(f"/auth/verify-email/{raw_token}")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}

    me2 = client.get("/me", headers=_auth(token))
    assert me2.json()["user"]["emailVerified"] is True


def test_verify_email_token_is_single_use(client, memory_backend):
    client.post("/auth/signup", json={
        "name": "Vera Verify", "email": "verify_singleuse1@example.com", "password": "verification-test-password-123",
    })
    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Verification token")

    first = client.post(f"/auth/verify-email/{raw_token}")
    assert first.status_code == 200

    second = client.post(f"/auth/verify-email/{raw_token}")
    assert second.status_code == 400
    assert second.json()["error_code"] == "verification_token_invalid"


def test_verify_email_rejects_garbage_token(client, memory_backend):
    r = client.post("/auth/verify-email/not-a-real-token")
    assert r.status_code == 400
    assert r.json()["error_code"] == "verification_token_invalid"


def test_verify_email_rejects_expired_token(client):
    """Direct db call (not through signup) so an already-expired token
    can be constructed deterministically, same technique
    test_invitations.py's expired-invitation test uses."""
    user = seed_user_and_token(email="verify_expired1@example.com", email_verified=False)
    now = int(time.time())
    token = _run(email_verification_db.create_verification_token(user["userId"], now - 100, ttl_seconds=50))

    r = client.post(f"/auth/verify-email/{token['rawToken']}")
    assert r.status_code == 400
    assert r.json()["error_code"] == "verification_token_invalid"


def test_resend_verification_sends_a_new_email(client, memory_backend):
    user = seed_user_and_token(email="verify_resend1@example.com", email_verified=False)

    r = client.post("/auth/resend-verification", headers=_auth(user["token"]))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "alreadyVerified": False}
    assert len(memory_backend.SENT) == 1
    assert memory_backend.SENT[0]["to"] == "verify_resend1@example.com"


def test_resend_verification_is_a_noop_once_already_verified(client, memory_backend):
    user = seed_user_and_token(email="verify_resend2@example.com", email_verified=True)

    r = client.post("/auth/resend-verification", headers=_auth(user["token"]))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "alreadyVerified": True}
    # No email sent — verifying again has nothing left to prove.
    assert len(memory_backend.SENT) == 0


def test_resend_verification_requires_authentication(client):
    r = client.post("/auth/resend-verification")
    assert r.status_code == 401


# ── permissions.py's REQUIRES_VERIFIED_EMAIL gate (Phase 4 plan §3 step 2) ──


def test_unverified_owner_cannot_invite_a_member(client):
    owner = seed_user_and_token(email="verify_gate_invite1@example.com", email_verified=False)
    r = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "someone@example.com", "role": "member"},
        headers=_auth(owner["token"]),
    )
    assert r.status_code == 403
    assert r.json()["error_code"] == "email_not_verified"


def test_unverified_owner_cannot_create_an_api_key(client):
    owner = seed_user_and_token(email="verify_gate_apikey1@example.com", email_verified=False)
    r = client.post("/api-keys", json={"scopes": ["runs:read"]}, headers=_auth(owner["token"]))
    assert r.status_code == 403
    assert r.json()["error_code"] == "email_not_verified"


def test_unverified_member_gets_insufficient_role_not_email_not_verified(client):
    """The role check runs FIRST — an unverified member who also lacks
    admin/owner rank should see the more fundamental INSUFFICIENT_ROLE,
    not a confusing EMAIL_NOT_VERIFIED for an action they couldn't have
    performed even with a verified email."""
    owner = seed_user_and_token(email="verify_gate_order_owner1@example.com")
    invite = _run(invitations_db.create_invitation(
        owner["orgId"], "verify_gate_order_member1@example.com", "member", owner["userId"], int(time.time()), 604800,
    ))
    r = client.post("/auth/signup", json={
        "name": "Plain Member", "email": "verify_gate_order_member1@example.com",
        "password": "verification-test-password-123", "invite_token": invite["rawToken"],
    })
    member_token = r.json()["token"]

    r2 = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "another@example.com", "role": "viewer"},
        headers=_auth(member_token),
    )
    assert r2.status_code == 403
    assert r2.json()["error_code"] == "insufficient_role"


def test_verified_owner_can_invite_and_create_api_key(client):
    """Sanity check that the gate is additive, not a regression — a
    normal verified account keeps working exactly as every pre-Phase-4
    test in this suite already assumes."""
    owner = seed_user_and_token(email="verify_gate_positive1@example.com", email_verified=True)
    r = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "someone2@example.com", "role": "member"},
        headers=_auth(owner["token"]),
    )
    assert r.status_code == 200
    r2 = client.post("/api-keys", json={"scopes": ["runs:read"]}, headers=_auth(owner["token"]))
    assert r2.status_code == 200


def test_invited_user_starts_already_verified(client, memory_backend):
    """Redeeming a real, single-use, emailed invitation link is itself
    proof of inbox control — db.create_user sets email_verified=True for
    the invited-signup path specifically (see its own docstring). Caught
    by a real end-to-end run of scripts/e2e_demo.py: without this, an
    invited admin was immediately EMAIL_NOT_VERIFIED-blocked from acting
    on the very permissions their invitation just granted, and a second,
    redundant verification email got queued on top of the invitation
    email that had just proven the same thing."""
    owner = seed_user_and_token(email="verify_invited1@example.com")
    invite = _run(invitations_db.create_invitation(
        owner["orgId"], "verify_invited_admin1@example.com", "admin", owner["userId"], int(time.time()), 604800,
    ))
    memory_backend.reset()  # only care about signup's own send, not the invitation email above

    signup = client.post("/auth/signup", json={
        "name": "Invited Admin", "email": "verify_invited_admin1@example.com",
        "password": "verification-test-password-123", "invite_token": invite["rawToken"],
    })
    assert signup.status_code == 200

    me = client.get("/me", headers=_auth(signup.json()["token"]))
    assert me.json()["user"]["emailVerified"] is True
    # No redundant verification email — the invitation itself already proved this.
    assert memory_backend.SENT == []

    # And the gate doesn't block them — they can act immediately.
    r = client.post(
        f"/orgs/{owner['orgId']}/invitations", json={"email": "verify_invited_downstream1@example.com", "role": "viewer"},
        headers=_auth(signup.json()["token"]),
    )
    assert r.status_code == 200


# ── unverified accounts are excluded from alert email fan-out ──────────


def test_unverified_owner_is_excluded_from_alert_recipients():
    """Direct db.alerts._recipients_for call — the narrowest, most
    deterministic way to verify the exclusion without also depending on
    notification_preferences defaults or the outbox/sender pipeline,
    which are already exercised for their own sake in
    tests/test_alerts.py / tests/test_email_delivery.py."""
    owner = seed_user_and_token(email="verify_gate_alert1@example.com", email_verified=False)

    async def _check():
        from db.engine import get_sessionmaker

        async with get_sessionmaker()() as session:
            return await alerts_db._recipients_for(session, owner["orgId"], "critical")

    recipients = _run(_check())
    assert recipients == []


def test_verified_owner_is_included_in_alert_recipients():
    owner = seed_user_and_token(email="verify_gate_alert2@example.com", email_verified=True)

    async def _check():
        from db.engine import get_sessionmaker

        async with get_sessionmaker()() as session:
            return await alerts_db._recipients_for(session, owner["orgId"], "critical")

    recipients = _run(_check())
    assert len(recipients) == 1
    assert recipients[0]["email"] == "verify_gate_alert2@example.com"
