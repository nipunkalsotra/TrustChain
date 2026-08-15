"""
auth.py — JWT issuance/verification for TrustChain accounts, and the
unified Principal resolution (JWT for humans, API keys for machines) that
every project-scoped endpoint depends on.

Tokens are signed with JWT_SECRET (config.Settings.jwt_secret — required,
no default). Startup fails closed if it's unset rather than falling back to
a random per-process secret: a random secret invalidates every session on
restart and differs across replicas, which is exactly the kind of bug that
looks fine in a single-process demo and breaks the moment there's more than
one process.

Settings are read lazily via get_settings() inside each function, not at
module import time — get_settings() validates on first call, and importing
this module (which happens transitively via `import main`) must not force
that validation before a caller (e.g. a test fixture) has had a chance to
set JWT_SECRET in the environment.

WHY project_id/org_id ARE EMBEDDED IN THE JWT rather than looked up per
request: the API service must not need a database round-trip merely to
authenticate (plan §6.1) — a user has exactly one org/project today
(auto-provisioned at signup, db.tenancy.provision_personal_org), so it's
stable for the token's lifetime. If multi-project-per-user ever ships,
re-login (or a token refresh) picks up a project switch; there's no UI for
that today, so it isn't a live concern yet.

WHY THE PRIMARY TOKEN STAYS 7-DAY-LIVED rather than the plan's specified
15-minute access token: the deployed frontend stores this token and
attaches it as a Bearer header indefinitely — it has no silent-refresh
logic (that's a frontend change, explicitly out of scope for this backend
pass). Shortening it here with nothing to renew it would force a real
logged-in user out every 15 minutes with no recovery path, a regression
this pass must not introduce. Instead, the plan's actual short-lived-
access + rotating-refresh mechanism is implemented as fully additive new
capability — refresh.py's issue_token_pair()/rotate_refresh_token() — for
the SDK/CLI and any future frontend to adopt, without changing what
/auth/login returns or how long it stays valid today.
"""

import logging
import time
from typing import Optional

import jwt
from fastapi import Header

from errors import ApiError, ErrorCode

from config import get_settings
from db.engine import current_org_id, current_project_id
from logging_config import bind_tenant_context

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days — see module docstring on why this hasn't shortened


def create_token(email: str, name: str, project_id: int, org_id: int, user_id: int = 0) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": email, "name": name, "project_id": project_id, "org_id": org_id, "user_id": user_id,
        "iat": now, "exp": now + _TOKEN_TTL_SECONDS,
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token, settings.jwt_secret, algorithms=[_ALGORITHM],
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )


class Principal:
    """The resolved identity+authority behind a request, regardless of
    which credential type produced it. `scopes` is None for a human/JWT
    session (full access to their own project via the web UI — scopes are
    a machine-credential concept, plan §11.3) and a list for an API key
    (least-privilege by default — see require_scope)."""

    def __init__(self, project_id: int, org_id: int, actor: str, scopes: Optional[list[str]] = None):
        self.project_id = project_id
        self.org_id = org_id
        self.actor = actor  # user email, or "api_key:<id>" — for audit_events/logging
        self.scopes = scopes

    @property
    def is_api_key(self) -> bool:
        return self.scopes is not None


class CurrentUser:
    """Human-session identity — kept as a distinct, narrower type from
    Principal for endpoints that are exclusively human-facing (auth
    itself, API key management) and must never accept a machine
    credential, however permissive its scopes."""

    def __init__(self, email: str, name: str, project_id: int, org_id: int, user_id: int):
        self.email = email
        self.name = name
        self.project_id = project_id
        self.org_id = org_id
        self.user_id = user_id


async def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    """FastAPI dependency — require a valid `Authorization: Bearer <JWT>`
    header (never an API key — see get_current_principal for endpoints
    that accept both)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "missing bearer token", ErrorCode.MISSING_BEARER_TOKEN)

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise ApiError(401, "invalid or expired token", ErrorCode.INVALID_TOKEN)

    # Sets the RLS session context for every DB transaction this request
    # opens from here on — see db/engine.py's module docstring. Also
    # binds the same identity onto every subsequent log line for this
    # request (F11 — see logging_config.py's bind_tenant_context).
    current_project_id.set(payload["project_id"])
    current_org_id.set(payload["org_id"])
    bind_tenant_context(payload["project_id"], payload["org_id"])

    return CurrentUser(
        email=payload["sub"], name=payload.get("name", ""),
        project_id=payload["project_id"], org_id=payload["org_id"],
        user_id=payload.get("user_id", 0),
    )


async def get_current_principal(authorization: Optional[str] = Header(None)) -> Principal:
    """FastAPI dependency accepting EITHER a human JWT or a machine API
    key (`tc_live_...` / `tc_test_...`) in the same Authorization header —
    used by every endpoint an SDK consumer can also call (runs, audit-log,
    scores), so a third-party agent never needs a human login."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "missing bearer token", ErrorCode.MISSING_BEARER_TOKEN)

    token = authorization.removeprefix("Bearer ").strip()

    if token.startswith("tc_live_") or token.startswith("tc_test_"):
        from db import tenancy

        key_info = await tenancy.verify_api_key(token, now=int(time.time()))
        if key_info is None:
            raise ApiError(401, "invalid, revoked, or expired API key", ErrorCode.INVALID_API_KEY)
        current_project_id.set(key_info["projectId"])
        current_org_id.set(key_info["orgId"])
        bind_tenant_context(key_info["projectId"], key_info["orgId"])
        return Principal(
            project_id=key_info["projectId"], org_id=key_info["orgId"],
            actor=f"api_key:{key_info['id']}", scopes=key_info["scopes"],
        )

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise ApiError(401, "invalid or expired token", ErrorCode.INVALID_TOKEN)

    current_project_id.set(payload["project_id"])
    current_org_id.set(payload["org_id"])
    bind_tenant_context(payload["project_id"], payload["org_id"])
    return Principal(project_id=payload["project_id"], org_id=payload["org_id"], actor=payload["sub"], scopes=None)


def require_scope(principal: Principal, scope: str) -> None:
    """No-op for a human/JWT principal (scopes=None => full access to
    their own project). For an API key, 403s if the requested scope
    wasn't granted at key-creation time — least privilege by default."""
    if principal.scopes is not None and scope not in principal.scopes:
        raise ApiError(403, f"API key is missing required scope: {scope}", ErrorCode.INSUFFICIENT_SCOPE)
