"""
db/invitations.py — invitation lifecycle (Phase 3 §5).

The invitation token is a bearer credential granting membership in an
org, so it gets the same treatment ApiKey.key_hash / RefreshToken.token_hash
already get: only sha256(token) is stored, the raw token exists exactly
once (in the email, sent by notifications/sender.py off the
alert_deliveries-style outbox — see main.py's invitation endpoints),
single-use enforced by a conditional UPDATE (not a read-then-write, so
two concurrent accept attempts can't both win), expiring, revocable.
"""

import hashlib
import secrets
import time
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from db.engine import get_sessionmaker, rls_bypass
from db.models import Invitation, Membership, Organization, User
from errors import ApiError, ErrorCode


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def create_invitation(org_id: int, email: str, role: str, invited_by: int, now: int, ttl_seconds: int) -> dict:
    """Returns {"id", "rawToken", "expiresAt"} — rawToken exists ONLY in
    this return value, here, at creation. The unique partial index on
    (org_id, lower(email)) WHERE accepted_at IS NULL AND revoked_at IS
    NULL (migration e2f3a4b5c6d7) is what turns a duplicate invite attempt
    into a clean IntegrityError instead of two competing pending rows."""
    raw_token = generate_token()
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        inv = Invitation(
            org_id=org_id, email=email.lower(), role=role, token_hash=_hash_token(raw_token),
            invited_by=invited_by, created_at=now, expires_at=now + ttl_seconds,
        )
        session.add(inv)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            raise ValueError("a pending invitation for this email already exists")
        await session.commit()
        inv_id = inv.id

    return {"id": inv_id, "rawToken": raw_token, "expiresAt": now + ttl_seconds}


async def _load_valid(session, token_hash: str, now: int) -> Optional[Invitation]:
    inv = (await session.execute(select(Invitation).where(Invitation.token_hash == token_hash))).scalar_one_or_none()
    if inv is None or inv.accepted_at is not None or inv.revoked_at is not None or inv.expires_at <= now:
        return None
    return inv


async def get_invitation_preview(raw_token: str, now: int) -> Optional[dict]:
    """GET /invitations/{token} — unauthenticated (the recipient has no
    account yet), so no app.current_org_id GUC is ever set for this
    connection. Runs inside an explicit RLS-bypass transaction (see
    db/engine.py::rls_bypass, the same escape hatch migration
    9f3a1c7d5e2b's policies already define) with a hand-written narrow
    projection — org name, role, inviter's name, expiry — never the full
    row. Any invalid/expired/revoked/already-used token returns None,
    which main.py maps to one identical 404 regardless of which of those
    four is actually true, so a scanner learns nothing from the
    difference (Phase 3 §5.1)."""
    token_hash = _hash_token(raw_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        with rls_bypass():
            inv = await _load_valid(session, token_hash, now)
            if inv is None:
                return None
            org = await session.get(Organization, inv.org_id)
            inviter = await session.get(User, inv.invited_by)

    return {
        "orgName": org.name if org else "",
        "role": inv.role,
        "invitedByName": inviter.name if inviter else "",
        "expiresAt": inv.expires_at,
        "email": inv.email,
    }


async def validate_for_signup(raw_token: str, signup_email: str, now: int) -> dict:
    """Used by POST /auth/signup's invite_token path. Raises ApiError
    directly (rather than returning None like the preview above) because
    signup needs to distinguish "bad token" from "right token, wrong
    email" — the latter is Phase 3 §5.3's explicit requirement that an
    invitation addressed to one person is not redeemable by another.

    Returns snake_case keys deliberately (org_id/invited_by/invitation_id),
    NOT this module's usual camelCase — this dict's only consumer is
    db.create_user's `invitation` parameter, whose own docstring documents
    exactly this shape ({"org_id", "role", "invited_by"}), which it
    forwards straight to tenancy.join_org_via_invitation with no key
    translation. It is never serialized directly to an HTTP response the
    way this module's other return values are, so there's no camelCase
    convention to match here — matching the ACTUAL CONSUMER is what
    matters. A prior version of this function returned camelCase keys,
    which silently broke every invite-token signup with a KeyError,
    caught by tests/test_invitations.py's real HTTP-level signup test."""
    token_hash = _hash_token(raw_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        with rls_bypass():
            inv = await _load_valid(session, token_hash, now)
            if inv is None:
                raise ApiError(404, "invitation not found, expired, or already used", ErrorCode.INVITATION_NOT_FOUND)
            if inv.email.lower() != signup_email.lower():
                raise ApiError(400, "invitation email does not match signup email", ErrorCode.INVITATION_EMAIL_MISMATCH)
            return {"org_id": inv.org_id, "role": inv.role, "invited_by": inv.invited_by, "invitation_id": inv.id}


async def mark_accepted(invitation_id: int, user_id: int, now: int) -> bool:
    """Conditional UPDATE ... WHERE accepted_at IS NULL — the single-use
    enforcement point. Two concurrent accept attempts both read the same
    'not yet accepted' state, but only one UPDATE can match the WHERE
    clause; the loser's rowcount is 0 and its caller must treat that as
    an already-used token, not silently succeed a second time."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Invitation).where(Invitation.id == invitation_id, Invitation.accepted_at.is_(None))
            .values(accepted_at=now, accepted_by=user_id)
        )
        await session.commit()
        return result.rowcount > 0


async def accept_for_existing_user(raw_token: str, user_id: int, user_email: str, now: int) -> dict:
    """POST /invitations/{token}/accept — the already-registered-user
    path (Phase 3 §5.4). Everything happens in ONE transaction: validating
    the token, creating the Membership, and marking the invitation
    accepted — a crash partway must not leave a membership with no
    accepted invitation, or vice versa."""
    from db.tenancy import join_org_via_invitation

    token_hash = _hash_token(raw_token)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        with rls_bypass():
            inv = await _load_valid(session, token_hash, now)
        if inv is None:
            raise ApiError(404, "invitation not found, expired, or already used", ErrorCode.INVITATION_NOT_FOUND)
        if inv.email.lower() != user_email.lower():
            raise ApiError(400, "invitation email does not match your account email", ErrorCode.INVITATION_EMAIL_MISMATCH)

        existing = await session.get(Membership, {"user_id": user_id, "org_id": inv.org_id})
        if existing is not None:
            raise ApiError(409, "already a member of this organization", ErrorCode.ALREADY_A_MEMBER)

        project = await join_org_via_invitation(session, user_id, inv.org_id, inv.role, inv.invited_by, now)

        result = await session.execute(
            update(Invitation).where(Invitation.id == inv.id, Invitation.accepted_at.is_(None))
            .values(accepted_at=now, accepted_by=user_id)
        )
        if result.rowcount == 0:
            # Lost the single-use race to a concurrent accept — roll back
            # the membership we just staged rather than leaving a second,
            # orphaned acceptance of an already-consumed token.
            await session.rollback()
            raise ApiError(409, "invitation already accepted", ErrorCode.INVITATION_ALREADY_ACCEPTED)

        await session.commit()

    return {"orgId": inv.org_id, "projectId": project.id, "role": inv.role}


async def list_invitations(org_id: int, status: Optional[str] = None, now: Optional[int] = None) -> list[dict]:
    now = now if now is not None else int(time.time())
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        rows = (await session.execute(
            select(Invitation).where(Invitation.org_id == org_id).order_by(Invitation.created_at.desc())
        )).scalars().all()

    def derived_status(inv: Invitation) -> str:
        if inv.revoked_at is not None:
            return "revoked"
        if inv.accepted_at is not None:
            return "accepted"
        if inv.expires_at <= now:
            return "expired"
        return "pending"

    results = [
        {
            "id": i.id, "email": i.email, "role": i.role, "status": derived_status(i),
            "createdAt": i.created_at, "expiresAt": i.expires_at, "invitedBy": i.invited_by,
        }
        for i in rows
    ]
    if status:
        results = [r for r in results if r["status"] == status]
    return results


async def revoke_invitation(invitation_id: int, org_id: int, revoked_by: int, now: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Invitation)
            .where(Invitation.id == invitation_id, Invitation.org_id == org_id, Invitation.accepted_at.is_(None),
                   Invitation.revoked_at.is_(None))
            .values(revoked_at=now, revoked_by=revoked_by)
        )
        await session.commit()
        return result.rowcount > 0


async def count_pending(org_id: int, now: int) -> int:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        rows = (await session.execute(
            select(Invitation.id).where(
                Invitation.org_id == org_id, Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None), Invitation.expires_at > now,
            )
        )).all()
        return len(rows)
