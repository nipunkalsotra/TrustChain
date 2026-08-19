"""
db/email_verification.py — email-verification token lifecycle (Phase 4
G1). Modeled directly on db/invitations.py: a bearer credential proving
control of an email address gets the identical treatment
ApiKey.key_hash / RefreshToken.token_hash / Invitation.token_hash
already get — only sha256(token) is stored, the raw token exists exactly
once (in the email), single-use enforced by a conditional UPDATE (not a
read-then-write, so two concurrent verify attempts can't both "win"),
expiring. See ADR-0014 for the original reasoning this reuses verbatim.
"""

import hashlib
import secrets
from typing import Optional

from sqlalchemy import select, update

from db.engine import get_sessionmaker
from db.models import EmailVerificationToken, User


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def create_verification_token(user_id: int, now: int, ttl_seconds: int) -> dict:
    """Returns {"rawToken", "expiresAt"} — rawToken exists ONLY here, at
    creation. Deliberately does not invalidate any still-outstanding
    prior token for this user (unlike Invitation's resend, which revokes
    then recreates to satisfy a uniqueness constraint) — there is no
    uniqueness constraint to satisfy here, and a user who requests a
    second verification email while the first is still unread should
    still be able to use either one; both simply prove the same thing."""
    raw_token = generate_token()
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        session.add(EmailVerificationToken(
            user_id=user_id, token_hash=_hash_token(raw_token),
            created_at=now, expires_at=now + ttl_seconds,
        ))
        await session.commit()

    return {"rawToken": raw_token, "expiresAt": now + ttl_seconds}


async def consume_token(raw_token: str, now: int) -> Optional[int]:
    """Validates and single-use-consumes a raw verification token,
    flipping users.email_verified in the SAME transaction as marking the
    token used — a crash between the two must not be observable as "token
    consumed but account still unverified" or vice versa. Returns the
    verified user's id on success, None for any invalid/expired/already-
    used token (main.py maps that uniformly to one error, same
    enumeration-safety reasoning as Invitation's preview lookup)."""
    token_hash = _hash_token(raw_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = (await session.execute(
            select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        )).scalar_one_or_none()
        if row is None or row.used_at is not None or row.expires_at <= now:
            return None

        result = await session.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.id == row.id, EmailVerificationToken.used_at.is_(None))
            .values(used_at=now)
        )
        if result.rowcount == 0:
            # Lost the single-use race to a concurrent consume attempt.
            await session.rollback()
            return None

        user = await session.get(User, row.user_id)
        if user is None:
            await session.rollback()
            return None
        user.email_verified = True
        await session.commit()
        return user.id


async def is_verified(user_id: int) -> bool:
    """Used by permissions.py's REQUIRES_VERIFIED_EMAIL gate — a fresh
    read each call (not JWT-embedded) so verifying takes effect
    immediately, without waiting for a new login."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        return bool((await session.execute(
            select(User.email_verified).where(User.id == user_id)
        )).scalar_one_or_none())


async def get_user_for_resend(user_id: int) -> Optional[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        return {"id": user.id, "email": user.email, "name": user.name, "emailVerified": user.email_verified}


async def cleanup_expired(now: int, older_than_seconds: int = 30 * 24 * 3600) -> int:
    """Mirrors idempotency.py's 24h-cleanup-job shape (Phase 2 gap #98) —
    used/expired verification tokens have zero value once well past their
    expiry and no reason to accumulate forever. Not wired into a
    scheduler by this change; exists so one can be added the same way the
    idempotency-key cleanup job already is, without a second design pass."""
    from sqlalchemy import delete

    cutoff = now - older_than_seconds
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            delete(EmailVerificationToken).where(EmailVerificationToken.expires_at < cutoff)
        )
        await session.commit()
        return result.rowcount
