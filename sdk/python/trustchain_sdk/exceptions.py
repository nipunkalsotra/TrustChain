"""trustchain_sdk.exceptions — one exception class per HTTP status
category the API actually returns (see backend/main.py), so callers can
catch by what happened rather than by parsing status codes themselves."""


class TrustChainError(Exception):
    """Base class for every error this SDK raises. `status_code` and
    `detail` mirror the API's own error shape (`{"detail": "..."}` or
    FastAPI's validation-error list) when the error came from an HTTP
    response; both are None for client-side errors (e.g. a stream
    timeout) that never got as far as the server.

    `error_code` (backend/errors.py's typed error taxonomy) is the
    machine-readable field this SDK's own exception CLASS can't express
    on its own — many logically distinct failures share one status code
    (e.g. every 401 here becomes AuthenticationError, whether it was a
    missing bearer token, an expired JWT, or a revoked API key), so this
    is what lets a caller branch on the SPECIFIC cause without parsing
    `detail` strings. None for responses from an older API version that
    predates error_code, or for client-side errors that never reached
    the server — always check for None before comparing."""

    def __init__(self, message: str, status_code: int = None, detail=None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


class BadRequestError(TrustChainError):
    """400 — an application-level input check failed (e.g. an empty
    `task`, main.py's own `if not body.task.strip()` guard) as opposed to
    a schema/type failure (422, see ValidationError below) — the request
    was well-formed JSON matching the expected shape, but the value
    itself isn't acceptable."""


class AuthenticationError(TrustChainError):
    """401 — missing/invalid credentials."""


class AuthorizationError(TrustChainError):
    """403 — valid credentials, insufficient scope (see auth.require_scope)."""


class NotFoundError(TrustChainError):
    """404 — resource doesn't exist, OR belongs to a different tenant
    (invariant I7: cross-tenant reads return 404, not 403, so as not to
    confirm another tenant's resource even exists)."""


class RateLimitError(TrustChainError):
    """429 — rate limit or monthly quota exceeded. `retry_after_seconds`
    is parsed from the response's Retry-After header when present."""

    def __init__(
        self, message: str, status_code: int = None, detail=None,
        retry_after_seconds: float = None, error_code: str = None,
    ):
        super().__init__(message, status_code, detail, error_code)
        self.retry_after_seconds = retry_after_seconds


class ConflictError(TrustChainError):
    """409 — Idempotency-Key reused with a different request body."""


class ValidationError(TrustChainError):
    """422 — request failed schema validation."""


class ServerError(TrustChainError):
    """5xx — the API's own fault, not the caller's. `detail` deliberately
    never contains internal exception text (see backend/main.py's F12
    fix) — it's always the fixed "internal error — see server logs"
    message; anything more specific needs the API's own logs."""


class StreamTimeoutError(TrustChainError):
    """The SSE stream (GET /stream/{run_id}) went quiet for longer than
    the client's configured timeout without reaching a terminal event —
    mirrors the server's own run_events.read_events timeout, but raised
    client-side since a client can time out independently of the server
    (e.g. a dropped connection the server hasn't noticed yet)."""
