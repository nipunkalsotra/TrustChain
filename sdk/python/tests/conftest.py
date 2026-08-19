"""tests/conftest.py — shared helpers for trustchain_sdk's own
real-backend integration tests (no mocking — see each test module's own
docstring).

Phase 4 G1 added a real email-verification gate
(backend/permissions.py::REQUIRES_VERIFIED_EMAIL) in front of
POST /api-keys — every fixture here that signs up a fresh user and
immediately needs to mint an API key now 403s with `email_not_verified`
unless that user is verified first. `verified_signup()` is the one place
that flow lives, so every call site (previously 3 separate near-
duplicates across test_client.py/test_instrumentation.py) picks it up
identically.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx

# Reaches into backend/ directly for the verification step — see
# verified_signup's own docstring for why this is a deliberate exception
# to this suite's otherwise real-HTTP-only philosophy.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
_BACKEND_PATH = str(_BACKEND_DIR)
if _BACKEND_PATH not in sys.path:
    sys.path.insert(0, _BACKEND_PATH)

# config.get_settings() needs JWT_SECRET (no default — fails startup if
# unset) and DATABASE_URL; both live in backend/.env, which nothing
# loads automatically when pytest's own cwd/rootdir is sdk/python/, not
# backend/ (config.py's own env_file=".env" is resolved relative to
# CWD, not this module's location — same trap CLAUDE.md's own notes
# describe for the schema-generation script). Loaded once, here, before
# _mark_verified's first call ever reaches config.get_settings() —
# os.environ.setdefault so a real ambient DATABASE_URL/JWT_SECRET (e.g.
# CI's own job-level env) always wins over whatever's in the file.
from dotenv import dotenv_values

for _key, _value in dotenv_values(_BACKEND_DIR / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)


def verified_signup(base_url: str, name: str, email: str, password: str) -> str:
    """Signs up a real user over real HTTP (exactly as before), then
    marks their email verified via a direct database call rather than a
    real HTTP round trip through POST /auth/verify-email/{token}.

    Why not go through the real endpoint like scripts/e2e_demo.py does:
    that script can rely on tailing the backend's own console-email-
    backend log file (EMAIL_BACKEND=console writes to .logs/fastapi.log
    for a plain-process local dev backend) because it always runs
    against that exact setup. THIS suite has no such guarantee — CI's
    sdk-integration job runs the backend via `docker compose up`, whose
    logs live in Docker's own log buffer, not a file this test process
    can read, and a local dev run might have EMAIL_BACKEND set to
    anything. A direct DB write sidesteps needing to know which email
    transport is in play at all.

    This is a deliberate, narrow exception to this suite's real-HTTP-only
    testing philosophy, not a precedent for testing everything this way:
    the verification MECHANISM itself (the token round trip through
    /auth/verify-email/{token}) is already exercised for real by
    backend/tests/test_email_verification.py. This suite's actual job is
    proving the SDK's HTTP client talks correctly to a live API — a job
    the verification gate would otherwise block from running at all if
    every fixture had to solve real email delivery first."""
    signup = httpx.post(
        f"{base_url}/auth/signup",
        json={"name": name, "email": email, "password": password},
        timeout=10.0,
    )
    assert signup.status_code == 200, signup.text
    token = signup.json()["token"]

    asyncio.run(_mark_verified(email))
    return token


async def _mark_verified(email: str) -> None:
    """Deliberately does NOT go through db.engine.get_sessionmaker() —
    that engine is an `@lru_cache` singleton (config.py/db/engine.py's
    documented pattern: whichever state exists at the FIRST call sticks
    for the rest of the process), and its connection pool binds to the
    event loop active when it's first created. verified_signup() is
    called once per test via a fresh asyncio.run() each time (a fresh
    event loop every time); reusing the cached engine across those
    breaks the SECOND call with "Future attached to a different loop" —
    a real error hit while building this fixture, not a hypothetical
    one. A throwaway asyncpg connection, opened and closed within this
    one call, sidesteps the whole problem — same reasoning
    backend/scripts/db_operator.py's own one-off DB access already uses
    a plain asyncpg connection rather than the app's cached engine."""
    import asyncpg
    from config import get_settings

    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET email_verified = true WHERE email = $1", email)
    finally:
        await conn.close()
