"""
refresh.py — short-lived access token + rotating refresh token, additive
alongside the existing long-lived primary JWT (see auth.py's module
docstring for why /auth/login's token hasn't changed). Intended for the
SDK/CLI and any future frontend update — nothing in the current backend
calls this yet except the new /auth/refresh and /auth/logout endpoints.

Reuse detection (plan §11.3): rotating a refresh token marks it `used_at`
and issues a new one in the same family. Presenting an already-used token
again means someone has a copy of a token that was already rotated away —
the whole family is revoked, forcing re-login, rather than letting a
stolen token keep working silently.
"""

import hashlib
import secrets
import time
import uuid

from sqlalchemy import select, update

from db.engine import get_sessionmaker
from db.models import RefreshToken

ACCESS_TTL_SECONDS = 15 * 60           # short-lived, per the plan
REFRESH_TTL_SECONDS = 30 * 24 * 3600   # 30 days


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_token_pair(user_id: int, email: str, name: str, project_id: int, org_id: int) -> dict:
    """Starts a new refresh-token family. Returns {"accessToken", "refreshToken", "expiresIn"}."""
    now = int(time.time())
    access_token = _short_lived_access_token(email, name, project_id, org_id, user_id, now)

    family_id = str(uuid.uuid4())
    raw_refresh = secrets.token_urlsafe(32)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        session.add(RefreshToken(
            user_id=user_id, family_id=family_id, token_hash=_hash(raw_refresh),
            created_at=now, expires_at=now + REFRESH_TTL_SECONDS,
        ))
        await session.commit()

    return {"accessToken": access_token, "refreshToken": raw_refresh, "expiresIn": ACCESS_TTL_SECONDS}


def _short_lived_access_token(email: str, name: str, project_id: int, org_id: int, user_id: int, now: int) -> str:
    import jwt as pyjwt

    from config import get_settings

    settings = get_settings()
    payload = {
        "sub": email, "name": name, "project_id": project_id, "org_id": org_id, "user_id": user_id,
        "iat": now, "exp": now + ACCESS_TTL_SECONDS,
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")


class RefreshError(Exception):
    """Raised for any invalid/expired/reused refresh token — the caller
    (main.py's /auth/refresh) maps this to a 401 uniformly; the specific
    reason is for logs, not the client."""


async def rotate_refresh_token(raw_refresh_token: str) -> dict:
    """Consumes one refresh token, returns a new {"accessToken",
    "refreshToken", "expiresIn"} pair in the same family. Raises
    RefreshError if the token is unknown, expired, revoked, or already
    used (the reuse case additionally revokes the whole family)."""
    now = int(time.time())
    token_hash = _hash(raw_refresh_token)

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        token_row = (await session.execute(stmt)).scalar_one_or_none()

        if token_row is None:
            raise RefreshError("unknown refresh token")
        if token_row.revoked_at is not None:
            raise RefreshError("refresh token family revoked")
        if token_row.expires_at <= now:
            raise RefreshError("refresh token expired")
        if token_row.used_at is not None:
            # Replay: this exact token was already rotated away once.
            # Revoke every token in the family so the legitimate holder
            # (who has the newer, still-valid token from that rotation)
            # is forced to re-authenticate, same as the attacker.
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == token_row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await session.commit()
            raise RefreshError("refresh token reuse detected — family revoked")

        token_row.used_at = now
        user_id, family_id = token_row.user_id, token_row.family_id

        new_raw_refresh = secrets.token_urlsafe(32)
        session.add(RefreshToken(
            user_id=user_id, family_id=family_id, token_hash=_hash(new_raw_refresh),
            created_at=now, expires_at=now + REFRESH_TTL_SECONDS,
        ))
        await session.commit()

    from db import tenancy

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from db.models import User

        user = await session.get(User, user_id)

    project = await tenancy.get_default_project_for_user(user_id)
    if project is None or user is None:
        raise RefreshError("user or project no longer exists")

    access_token = _short_lived_access_token(user.email, user.name, project["projectId"], project["orgId"], user_id, now)
    return {"accessToken": access_token, "refreshToken": new_raw_refresh, "expiresIn": ACCESS_TTL_SECONDS}


async def revoke_family_for_token(raw_refresh_token: str) -> None:
    """Explicit logout — revokes the whole family the given refresh token
    belongs to, not just that one token, so 'log out' actually invalidates
    every access token this session's chain could still mint."""
    now = int(time.time())
    token_hash = _hash(raw_refresh_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(RefreshToken.family_id).where(RefreshToken.token_hash == token_hash)
        family_id = (await session.execute(stmt)).scalar_one_or_none()
        if family_id is None:
            return
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
