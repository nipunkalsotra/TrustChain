"""
db/password_reset.py — password-reset token lifecycle (Phase 4 G2).
Same discipline as db/email_verification.py/db/invitations.py: only
sha256(token) is stored, single-use via a conditional UPDATE, expiring.

Two invariants main.py's endpoints depend on and must not weaken (Phase
4 plan §3 step 3):
  1. POST /auth/forgot-password returns an IDENTICAL response whether or
     not the account exists — create_reset_token_if_exists (not a
     separate "does this email exist" call main.py could branch on)
     is what makes that the only code shape available, not just a
     policy someone has to remember to follow at the call site.
  2. A successful reset invalidates every existing refresh token for
     that user (refresh.py::revoke_all_for_user) — a session opened
     before the password changed must not silently keep working after.
"""

import hashlib
import secrets
from typing import Optional

from sqlalchemy import select, update

from db import hash_password
from db.engine import get_sessionmaker
from db.models import PasswordResetToken, User


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def create_reset_token_if_exists(email: str, now: int, ttl_seconds: int) -> Optional[dict]:
    """Returns {"userId", "name", "rawToken", "expiresAt"} if the email
    belongs to a real account, else None — main.py's /auth/forgot-password
    calls this and then returns the SAME {"ok": true}-shaped response
    either way, only actually queueing an email in the Some case. This
    function, not the caller, is the single place "does this email exist"
    is ever branched on, so a future call site can't reintroduce an
    enumeration oracle by checking existence itself first."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            return None

        raw_token = generate_token()
        session.add(PasswordResetToken(
            user_id=user.id, token_hash=_hash_token(raw_token),
            created_at=now, expires_at=now + ttl_seconds,
        ))
        await session.commit()
        return {"userId": user.id, "name": user.name, "email": user.email, "rawToken": raw_token, "expiresAt": now + ttl_seconds}


async def reset_password(raw_token: str, new_password: str, now: int) -> Optional[int]:
    """Validates and single-use-consumes a raw reset token, then
    overwrites the user's password_hash — all in one transaction (a crash
    partway must not leave "token consumed, password unchanged" or vice
    versa). Returns the affected user's id on success, None for any
    invalid/expired/already-used token. Does NOT itself revoke refresh
    tokens — main.py's endpoint calls refresh.revoke_all_for_user
    separately, after this commits, since that's a different table this
    module has no reason to know about."""
    token_hash = _hash_token(raw_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = (await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )).scalar_one_or_none()
        if row is None or row.used_at is not None or row.expires_at <= now:
            return None

        result = await session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.id == row.id, PasswordResetToken.used_at.is_(None))
            .values(used_at=now)
        )
        if result.rowcount == 0:
            # Lost the single-use race to a concurrent reset attempt.
            await session.rollback()
            return None

        user = await session.get(User, row.user_id)
        if user is None:
            await session.rollback()
            return None
        user.password_hash = hash_password(new_password)
        await session.commit()
        return user.id
