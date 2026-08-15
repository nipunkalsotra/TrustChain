"""
tests/test_auth_pwned.py — Have I Been Pwned breach check (auth_pwned.py,
plan §11.3's "optional breach check against Have I Been Pwned using the
k-anonymity range API"), tested against the REAL api.pwnedpasswords.com
endpoint, not a mock — matching this project's "verify against real
infra" discipline for anything touching the network (see
tests/test_resilient_provider.py's own docstring for the same reasoning
applied to a different external dependency).

tests/conftest.py disables the check (CHECK_PWNED_PASSWORDS=false) for
the suite by default, since most existing tests reuse a handful of fixed
passwords that are themselves real breach hits — every test in this file
re-enables it explicitly via monkeypatch, the same pattern
tests/test_rate_limiting.py uses for its own settings overrides.
"""

import uuid

import httpx
import pytest

from auth_pwned import is_password_pwned

RANGE_API_URL = "https://api.pwnedpasswords.com/range/5BAA6"


def _hibp_is_reachable() -> bool:
    try:
        r = httpx.get(RANGE_API_URL, timeout=3.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


requires_hibp = pytest.mark.skipif(
    not _hibp_is_reachable(), reason="api.pwnedpasswords.com not reachable"
)


def _strong_unique_password() -> str:
    """Astronomically unlikely to ever appear in a real breach corpus —
    unlike a memorable/guessable string, this is exactly the kind of
    password the check should NOT reject."""
    return f"tc_{uuid.uuid4().hex}{uuid.uuid4().hex}"


# ── auth_pwned.is_password_pwned — direct, real API ─────────────────────

@requires_hibp
def test_is_password_pwned_true_for_a_known_breached_password():
    import asyncio

    # "password" is one of the most common breached passwords in
    # existence — if this ever comes back False, the k-anonymity
    # matching logic itself is broken, not just an edge case.
    assert asyncio.run(is_password_pwned("password")) is True


@requires_hibp
def test_is_password_pwned_false_for_a_strong_unique_password():
    import asyncio

    assert asyncio.run(is_password_pwned(_strong_unique_password())) is False


def test_is_password_pwned_fails_open_on_network_error(monkeypatch):
    """No real network involved here — verifies the fail-open contract
    itself (module docstring), which by definition can't be verified by
    hitting the real, working API."""
    import asyncio

    import auth_pwned

    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise httpx.ConnectTimeout("simulated network failure")

    monkeypatch.setattr(auth_pwned.httpx, "AsyncClient", lambda **kw: _BrokenClient())

    assert asyncio.run(is_password_pwned("anything")) is False


# ── POST /auth/signup, check re-enabled ──────────────────────────────────

@requires_hibp
def test_signup_rejects_a_known_breached_password(client, monkeypatch):
    import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "check_pwned_passwords", True)

    r = client.post(
        "/auth/signup",
        json={"name": "Breach Test", "email": f"breach-{uuid.uuid4().hex[:8]}@example.com", "password": "password123"},
    )

    assert r.status_code == 400, r.text
    assert r.json()["error_code"] == "password_pwned"


@requires_hibp
def test_signup_accepts_a_strong_password_when_check_enabled(client, monkeypatch):
    import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "check_pwned_passwords", True)

    r = client.post(
        "/auth/signup",
        json={
            "name": "Strong Password",
            "email": f"strong-{uuid.uuid4().hex[:8]}@example.com",
            "password": _strong_unique_password(),
        },
    )

    assert r.status_code == 200, r.text


def test_signup_allows_a_breached_password_when_check_disabled(client, monkeypatch):
    """Documents the 'optional' half of the plan's wording: the toggle is
    a real toggle, not just a default. No @requires_hibp — with the check
    disabled, this must succeed even if api.pwnedpasswords.com is
    unreachable, which is the whole point of it being possible to turn
    off."""
    import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "check_pwned_passwords", False)

    r = client.post(
        "/auth/signup",
        json={"name": "Disabled Check", "email": f"disabled-{uuid.uuid4().hex[:8]}@example.com", "password": "password123"},
    )

    assert r.status_code == 200, r.text
