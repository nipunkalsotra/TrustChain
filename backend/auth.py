"""
auth.py — JWT issuance/verification for TrustChain accounts.

Tokens are signed with JWT_SECRET (env var). If unset, a random secret is
generated once at process startup — fine for solo local dev, but every
restart invalidates existing sessions, and it won't work across multiple
backend workers/processes. Set JWT_SECRET explicitly for anything beyond
that.
"""

import logging
import os
import secrets
import time
from typing import Optional

import jwt
from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

_JWT_SECRET = os.getenv("JWT_SECRET")
if not _JWT_SECRET:
    _JWT_SECRET = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET not set — using a random secret for this process only. "
        "Every restart invalidates existing sessions; set JWT_SECRET in .env "
        "for anything beyond solo local dev."
    )

_ALGORITHM = "HS256"
_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def create_token(email: str, name: str) -> str:
    now = int(time.time())
    payload = {"sub": email, "name": name, "iat": now, "exp": now + _TOKEN_TTL_SECONDS}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _JWT_SECRET, algorithms=[_ALGORITHM])


class CurrentUser:
    def __init__(self, email: str, name: str):
        self.email = email
        self.name = name


async def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    """FastAPI dependency — require a valid `Authorization: Bearer <token>` header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    return CurrentUser(email=payload["sub"], name=payload.get("name", ""))
