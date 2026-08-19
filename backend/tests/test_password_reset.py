"""
tests/test_password_reset.py — password reset (Phase 4 G2), over the
real HTTP surface plus the memory email backend, same pattern as
tests/test_email_verification.py. Real Postgres via Testcontainers, no
mocked database.

Two invariants get their own dedicated test, since they're the ones
main.py's module comment calls out as non-negotiable (Phase 4 plan §3
step 3): the forgot-password response must not reveal whether an account
exists, and a successful reset must kill every existing refresh-token
session.
"""

import asyncio
import re
import time

import pytest

from tests.conftest import seed_user_and_token

import db.password_reset as password_reset_db


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def memory_backend(monkeypatch):
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


def test_forgot_password_unknown_email_returns_ok_and_sends_nothing(client, memory_backend):
    r = client.post("/auth/forgot-password", json={"email": "nobody_here_at_all@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert memory_backend.SENT == []


def test_forgot_password_known_email_returns_identical_response_and_sends_a_real_email(client, memory_backend):
    seed_user_and_token(email="reset_known1@example.com")

    unknown = client.post("/auth/forgot-password", json={"email": "still_nobody@example.com"})
    known = client.post("/auth/forgot-password", json={"email": "reset_known1@example.com"})

    # Byte-identical response either way — Phase 4 plan §3 step 3's
    # explicit non-negotiable: an attacker probing emails must learn
    # nothing from the HTTP response, only from an out-of-band signal
    # (whether an email arrived) this test checks separately below.
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json() == {"ok": True}

    assert len(memory_backend.SENT) == 1
    sent = memory_backend.SENT[0]
    assert sent["to"] == "reset_known1@example.com"
    assert "reset" in sent["subject"].lower()
    assert "Reset token:" in sent["textBody"]


def test_reset_password_changes_the_password(client, memory_backend):
    seed_user_and_token(email="reset_flow1@example.com")
    client.post("/auth/forgot-password", json={"email": "reset_flow1@example.com"})
    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Reset token")

    r = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "reset-test-password-456"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    old_login = client.post("/auth/login", json={"email": "reset_flow1@example.com", "password": "testpassword123"})
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login", json={"email": "reset_flow1@example.com", "password": "reset-test-password-456"},
    )
    assert new_login.status_code == 200


def test_reset_token_is_single_use(client, memory_backend):
    seed_user_and_token(email="reset_singleuse1@example.com")
    client.post("/auth/forgot-password", json={"email": "reset_singleuse1@example.com"})
    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Reset token")

    first = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "reset-test-password-456"})
    assert first.status_code == 200

    second = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "reset-test-password-789"})
    assert second.status_code == 400
    assert second.json()["error_code"] == "reset_token_invalid"


def test_reset_password_rejects_garbage_token(client):
    r = client.post("/auth/reset-password/not-a-real-token", json={"new_password": "reset-test-password-456"})
    assert r.status_code == 400
    assert r.json()["error_code"] == "reset_token_invalid"


def test_reset_password_rejects_expired_token(client):
    user = seed_user_and_token(email="reset_expired1@example.com")
    now = int(time.time())
    token = _run(password_reset_db.create_reset_token_if_exists("reset_expired1@example.com", now - 100, ttl_seconds=50))
    assert token is not None

    r = client.post(f"/auth/reset-password/{token['rawToken']}", json={"new_password": "reset-test-password-456"})
    assert r.status_code == 400
    assert r.json()["error_code"] == "reset_token_invalid"
    del user  # only needed to have created the account


def test_reset_password_invalidates_existing_refresh_tokens(client, memory_backend):
    """Phase 4 plan §3 step 3's second non-negotiable: a session opened
    before the password changed must not silently keep working after."""
    user = seed_user_and_token(email="reset_sessions1@example.com")
    pair = client.post("/auth/token-pair", headers=_auth(user["token"])).json()
    old_refresh_token = pair["refresh_token"]

    client.post("/auth/forgot-password", json={"email": "reset_sessions1@example.com"})
    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Reset token")
    r = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "reset-test-password-456"})
    assert r.status_code == 200

    refreshed = client.post("/auth/refresh", json={"refresh_token": old_refresh_token})
    assert refreshed.status_code == 401
    assert refreshed.json()["error_code"] == "invalid_refresh_token"


def test_reset_password_rejects_a_known_breached_password(client, monkeypatch, memory_backend):
    """Same real-HIBP-API technique tests/test_auth_pwned.py uses for
    signup — check_pwned_passwords is off by default across this suite
    (see conftest.py's own comment on why), re-enabled here explicitly."""
    from config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "check_pwned_passwords", True)

    seed_user_and_token(email="reset_pwned1@example.com")
    client.post("/auth/forgot-password", json={"email": "reset_pwned1@example.com"})
    raw_token = _extract_token(memory_backend.SENT[0]["textBody"], "Reset token")

    r = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "password"})
    assert r.status_code == 400
    assert r.json()["error_code"] == "password_pwned"

    # The token must still be usable afterward — a rejected password
    # attempt is not a "used" attempt.
    r2 = client.post(f"/auth/reset-password/{raw_token}", json={"new_password": "reset-test-password-456"})
    assert r2.status_code == 200
