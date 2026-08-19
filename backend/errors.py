"""
errors.py — typed error taxonomy for the API.

Every HTTPException raised across this backend used to carry only a
human-readable `detail` string and an HTTP status code — enough for a
person reading the response, not enough for a CLIENT to reliably branch
on. Many logically distinct failures share one status code: 400 covers
both "task cannot be empty" and "invalid API key scope request", 401
covers "missing bearer token", "expired JWT", AND "revoked API key", 404
covers "run not found" and "step not found". A caller that wants to tell
these apart has no choice but to string-match `detail` — brittle, and
exactly the coupling this module exists to remove (the SDKs' exception
classes, sdk/python/trustchain_sdk/exceptions.py and
sdk/typescript/src/errors.ts, currently map status code -> exception
class only, one class covering many unrelated causes).

ApiError additionally carries a stable `error_code` (see ErrorCode
below), surfaced as a SIBLING top-level field in the response body
(`{"detail": "...", "error_code": "..."}`), never folded into `detail`
itself — so nothing that already reads `detail` (F12's leak-prevention
test, existing SDK status-code-based mapping) has to change; error_code
is purely additive. A caller that doesn't know about it yet sees exactly
what it saw before.
"""

from enum import Enum
from typing import Optional

from fastapi import HTTPException


class ErrorCode(str, Enum):
    # Auth (auth.py)
    MISSING_BEARER_TOKEN = "missing_bearer_token"
    INVALID_TOKEN = "invalid_token"
    INVALID_API_KEY = "invalid_api_key"
    INSUFFICIENT_SCOPE = "insufficient_scope"

    # Rate limiting / login backoff (rate_limit.py)
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    LOGIN_BACKOFF_ACTIVE = "login_backoff_active"

    # Signup / login / refresh (main.py)
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    INVALID_CREDENTIALS = "invalid_credentials"
    INVALID_REFRESH_TOKEN = "invalid_refresh_token"
    INSUFFICIENT_ROLE = "insufficient_role"
    PASSWORD_PWNED = "password_pwned"

    # API keys
    API_KEY_CREATE_FAILED = "api_key_create_failed"
    API_KEY_NOT_FOUND = "api_key_not_found"

    # Runs / steps
    TASK_EMPTY = "task_empty"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    QUOTA_EXCEEDED = "quota_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    RUN_NOT_FOUND = "run_not_found"
    RUN_NOT_COMPLETE = "run_not_complete"
    STEP_NOT_FOUND = "step_not_found"

    # V1 chain bridge / verify
    BRIDGE_UNAVAILABLE = "bridge_unavailable"
    TAMPER_DEMO_AGENT_NOT_FOUND = "tamper_demo_agent_not_found"

    # Agent identity (V2)
    AGENT_REGISTRATION_FAILED = "agent_registration_failed"
    AGENT_VERIFICATION_FAILED = "agent_verification_failed"

    # Organizations / projects (Phase 3 §4, §9.2-9.3)
    ORG_NOT_FOUND = "org_not_found"
    PROJECT_NOT_FOUND = "project_not_found"
    CANNOT_DELETE_LAST_PROJECT = "cannot_delete_last_project"
    CANNOT_DELETE_ONLY_ORG = "cannot_delete_only_org"
    ORG_DELETED = "org_deleted"
    PROJECT_DELETED = "project_deleted"

    # Membership (Phase 3 §4.3, §5.5)
    MEMBER_NOT_FOUND = "member_not_found"
    INVALID_ROLE = "invalid_role"
    CANNOT_MODIFY_OWN_ROLE = "cannot_modify_own_role"
    CANNOT_REMOVE_LAST_OWNER = "cannot_remove_last_owner"
    ALREADY_A_MEMBER = "already_a_member"
    MEMBERSHIP_REVOKED = "membership_revoked"

    # Invitations (Phase 3 §5)
    INVITATION_NOT_FOUND = "invitation_not_found"
    INVITATION_EXPIRED = "invitation_expired"
    INVITATION_ALREADY_ACCEPTED = "invitation_already_accepted"
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_EMAIL_MISMATCH = "invitation_email_mismatch"
    TOO_MANY_PENDING_INVITATIONS = "too_many_pending_invitations"

    # Alerts (Phase 3 §7)
    ALERT_NOT_FOUND = "alert_not_found"
    ALERT_ALREADY_RESOLVED = "alert_already_resolved"
    ALERT_NOT_RESOLVABLE = "alert_not_resolvable"

    # Integrity (Phase 3 §6)
    INTEGRITY_CHECK_FAILED = "integrity_check_failed"
    RUN_NOT_VERIFIABLE = "run_not_verifiable"

    # Catch-all for genuinely unexpected failures — a client can't act
    # differently based on WHICH endpoint hit this, so unlike everything
    # above it stays one generic code rather than one per call site.
    INTERNAL_ERROR = "internal_error"


class ApiError(HTTPException):
    """Drop-in replacement for `raise HTTPException(status_code=..., detail=...)`
    — same constructor shape plus a required `error_code`. main.py's
    `api_error_handler` (registered via `@app.exception_handler(ApiError)`)
    is what actually renders `error_code` into the response body; this
    class just carries it from the raise site to that handler."""

    def __init__(self, status_code: int, detail: str, error_code: ErrorCode, headers: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code
