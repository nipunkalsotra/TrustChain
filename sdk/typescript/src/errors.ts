/**
 * errors.ts — one error class per HTTP status category the API actually
 * returns (see backend/main.py), mirroring the Python SDK's
 * trustchain_sdk.exceptions exactly so both SDKs teach the same mental
 * model of the API's error shape.
 */

export class TrustChainError extends Error {
  readonly statusCode: number | undefined;
  readonly detail: unknown;

  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message);
    this.name = "TrustChainError";
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

/** 400 — an application-level input check failed (e.g. an empty `task`,
 * main.py's own `if not body.task.strip()` guard), as opposed to a
 * schema/type failure (422, see ValidationError) — the request was
 * well-formed JSON matching the expected shape, but the value itself
 * isn't acceptable. */
export class BadRequestError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "BadRequestError";
  }
}

/** 401 — missing/invalid credentials. */
export class AuthenticationError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "AuthenticationError";
  }
}

/** 403 — valid credentials, insufficient scope (see auth.require_scope). */
export class AuthorizationError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "AuthorizationError";
  }
}

/** 404 — resource doesn't exist, OR belongs to a different tenant
 * (invariant I7: cross-tenant reads return 404, not 403, so as not to
 * confirm another tenant's resource even exists), OR (for a run) exists
 * but hasn't reached a terminal status yet. */
export class NotFoundError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "NotFoundError";
  }
}

/** 409 — Idempotency-Key reused with a different request body. */
export class ConflictError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "ConflictError";
  }
}

/** 422 — request failed schema validation. */
export class ValidationError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "ValidationError";
  }
}

/** 429 — rate limit or monthly quota exceeded. `retryAfterSeconds` is
 * parsed from the response's Retry-After header when present. */
export class RateLimitError extends TrustChainError {
  readonly retryAfterSeconds: number | undefined;

  constructor(message: string, statusCode?: number, detail?: unknown, retryAfterSeconds?: number) {
    super(message, statusCode, detail);
    this.name = "RateLimitError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** 5xx — the API's own fault, not the caller's. `detail` deliberately
 * never contains internal exception text (see backend/main.py's F12
 * fix) — it's always the fixed "internal error — see server logs"
 * message; anything more specific needs the API's own logs. */
export class ServerError extends TrustChainError {
  constructor(message: string, statusCode?: number, detail?: unknown) {
    super(message, statusCode, detail);
    this.name = "ServerError";
  }
}

/** The SSE stream (GET /stream/{run_id}) went quiet for longer than the
 * client's configured timeout without the connection closing — mirrors
 * the server's own run_events.read_events timeout, but raised
 * client-side since a client can time out independently of the server. */
export class StreamTimeoutError extends TrustChainError {
  constructor(message: string) {
    super(message);
    this.name = "StreamTimeoutError";
  }
}
