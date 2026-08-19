"""
main.py  —  TrustChain FastAPI backend

Endpoints:
  POST /run-agent          → start pipeline, returns run_id
  GET  /stream/{run_id}    → SSE stream of agent events
  GET  /audit-log          → anchored steps, from the read model (Postgres)
  GET  /trust-scores       → all 4 agent scores for a run, from the read model
  POST /verify             → hash integrity check against on-chain record
  GET  /chain-status       → Monad connection status
  GET  /health             → quick health check

Run:
  uvicorn main:app --reload --port 8000
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

import auth
import auth_pwned
import db
import deprecation
import observability
import rate_limit
import refresh
import run_events
import alert_events
from db import idempotency, read_model, tenancy
from db import alerts as alerts_db
from db import email_verification as email_verification_db
from db import invitations as invitations_db
from db import orgs as orgs_db
from db import password_reset as password_reset_db
from errors import ApiError, ErrorCode
from config import get_settings
from logging_config import configure_logging, get_logger, bind_run_id, CorrelationIdMiddleware
from blockchain.client import get_bridge
from agents.pipeline import run_pipeline
from agents.base import make_run_id, log_step
from blockchain import identity_writer
import membership_cache
import permissions

configure_logging(log_level=get_settings().log_level, json_logs=get_settings().environment != "development")
logger = get_logger(__name__)

observability.init_sentry(
    get_settings().sentry_dsn, get_settings().environment, get_settings().sentry_traces_sample_rate,
)
observability.init_tracing(get_settings().otel_service_name, get_settings().otel_exporter_otlp_endpoint)



# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI app + lifespan
# ─────────────────────────────────────────────────────────────────────────────

# F14 (Graceful shutdown): tasks POST /run-agent hands to
# asyncio.create_task() are fully detached — nothing tracks them, and
# uvicorn's own graceful-shutdown accounting only covers in-flight HTTP
# requests/responses, not arbitrary background tasks the app spawned. A
# task cancelled or abandoned mid-run never reaches
# _run_pipeline_background's `except Exception` clause (CancelledError
# derives from BaseException, not Exception) or its db.fail_run() calls,
# so without this the run stays at status='running' in Postgres forever —
# the exact failure mode plan F14 exists to close. run_id -> Task (not a
# plain set) so the shutdown path below can name/fail the SPECIFIC runs
# still in flight rather than just counting them.
_background_tasks: dict[asyncio.Task, str] = {}


def _spawn_background_pipeline_run(task: str, run_id: str, org_id: int) -> asyncio.Task:
    bg_task = asyncio.create_task(_run_pipeline_background(task, run_id, org_id))
    _background_tasks[bg_task] = run_id
    bg_task.add_done_callback(lambda t: _background_tasks.pop(t, None))
    return bg_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("backend_starting")
    try:
        bridge = get_bridge()
        logger.info("bridge_ready", wallet=bridge.account.address)
    except Exception as e:
        logger.error("bridge_init_failed_non_fatal", error=str(e))
    yield
    logger.info("backend_shutting_down", in_flight_runs=len(_background_tasks))
    if _background_tasks:
        settings = get_settings()
        pending_tasks = list(_background_tasks.keys())
        _done, pending = await asyncio.wait(pending_tasks, timeout=settings.api_shutdown_drain_timeout_seconds)
        for bg_task in pending:
            run_id = _background_tasks.get(bg_task)
            bg_task.cancel()
            logger.warning("shutdown_drain_timeout_abandoning_run", run_id=run_id)
            if run_id is None:
                continue
            try:
                # fail_run_if_still_running, not fail_run: cancellation and
                # this write aren't atomic with whatever the task itself
                # might still be doing, so this guards against clobbering
                # a run that raced to a real completion in that narrow
                # window with a spurious "interrupted by shutdown" status.
                await db.fail_run_if_still_running(run_id, "server shutdown before run completed", int(time.time()))
            except Exception:
                logger.exception("shutdown_fail_run_failed", run_id=run_id)


app = FastAPI(
    title="TrustChain API",
    description="Multi-agent AI with every step recorded on Monad testnet",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CorrelationIdMiddleware)

# CORS: explicit allowlist only. "*" combined with allow_credentials=True is
# invalid per the CORS spec (browsers reject it outright), so it can never be
# used to also cover a deployed frontend URL — that URL must be listed here.
# Add your deployed frontend origin (e.g. Vercel URL) via FRONTEND_URL once known.
_extra_origin = get_settings().frontend_url
_allowed_origins = [
    "http://localhost:3000",     # Next.js dev server
    "http://localhost:3001",     # Next.js alt port
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
if _extra_origin:
    _allowed_origins.append(_extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Cache-Control"],
)

# Instruments the app with OpenTelemetry spans (one per request) — needs
# the app instance, so this runs separately from init_tracing() above,
# which only sets up the process-wide TracerProvider/exporter.
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Records HTTP request count/duration for /metrics. `path` uses the
    matched route template (request.url.path would put run_id/key_id
    straight into a label value — unbounded cardinality as more runs/keys
    accumulate); falls back to the raw path only for genuinely unmatched
    routes (404s), which are rare enough not to matter."""
    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start

    route = request.scope.get("route")
    path = route.path if route is not None else request.url.path
    observability.HTTP_REQUESTS_TOTAL.labels(
        method=request.method, path=path, status=response.status_code
    ).inc()
    observability.HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, path=path).observe(duration)
    return response


@app.middleware("http")
async def _deprecation_headers_middleware(request: Request, call_next):
    """Adds Deprecation/Sunset/Link headers per deprecation.DEPRECATED_ROUTES
    — see that module and docs/api-deprecation-policy.md. A no-op today
    (that list is empty), always run so a future entry takes effect with
    no other code change."""
    response = await call_next(request)
    await deprecation.add_deprecation_headers(request, response)
    return response


@app.get("/metrics")
async def metrics():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    """Renders error_code as a SIBLING field alongside `detail`, not a
    replacement for it — see errors.py's module docstring for why this
    has to stay additive. A plain `raise HTTPException(...)` (not yet
    migrated to ApiError, or raised by FastAPI/Starlette itself — a 422
    from Pydantic validation, a 404 from an unmatched route) is NOT
    caught here; it falls through to FastAPI's own default handler and
    gets a body with no error_code field at all, same as before this
    existed."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": exc.error_code.value},
        headers=exc.headers,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  API versioning (F-item, plan §13.4/Appendix A): every route below lives
#  on `router`, mounted TWICE at the bottom of this file — once unprefixed
#  (legacy shape, kept working indefinitely for the existing frontend and
#  any pre-versioning integration — "never break the host application" is
#  a design principle for the SDK, and it applies here too) and once under
#  /v1, the new canonical, documented surface. Both point at the exact
#  same handler functions — there is no behavior difference, only the
#  path prefix. /health, /ready, and /metrics stay on `app` directly,
#  unversioned: they're infrastructure probes, not the business API
#  surface Appendix A actually versions.
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import APIRouter
router = APIRouter()

# A handful of routes' legacy (unprefixed) names don't literally match
# Appendix A's documented /v1 path for the same operation (POST
# /api-keys vs. the spec's POST /v1/keys; POST /run-agent vs. POST
# /v1/runs; GET /stream/{run_id} vs. GET /v1/runs/{id}/stream) — found by
# diffing this file's actual routes against the plan's Appendix A table
# directly, not by inspection alone. `router`'s dual-mount above covers
# "same path, with or without /v1/" fine, but can't rename a path only
# under one prefix. v1_only_router holds exactly these 3 differently-named
# aliases, mounted under /v1 ONLY (not unprefixed) below — the legacy
# names keep working exactly as before (still on `router`, still
# dual-mounted), this just adds the additional, spec-exact name where the
# two didn't already match.
v1_only_router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
#  Platform admin-action audit log (db.models.AuditEvent) — distinct from
#  the on-chain agent audit log. Covers key issuance/revocation and other
#  authority-affecting actions per the plan's T3 (insider/operator) threat
#  mitigation: "admin actions are themselves audited." Best-effort — a
#  logging failure must never block the action it's describing.
# ─────────────────────────────────────────────────────────────────────────────

async def audit_log_admin_action(current_user: "auth.CurrentUser", action: str, target: Optional[str] = None) -> None:
    try:
        from db.engine import get_sessionmaker
        from db.models import AuditEvent

        async with get_sessionmaker()() as session:
            session.add(AuditEvent(
                actor_id=current_user.user_id, org_id=current_user.org_id,
                action=action, target=target, created_at=int(time.time()),
            ))
            await session.commit()
    except Exception as e:
        logger.error("audit_log_write_failed", action=action, error=str(e))


def get_bridge_or_503():
    """get_bridge() raises if V1's PRIVATE_KEY/MONAD_RPC_URL aren't
    configured — real Monad testnet secrets, never set in CI (see
    .github/workflows/test.yml) and not required for any environment
    that only runs V2. Every V1 endpoint (/verify, /verify/tamper-demo,
    /verify-audit, /health) needs to treat that as a clean, deliberate
    503, not let it propagate as FastAPI's generic unhandled-exception
    500 from several near-identical call sites — found via real
    Schemathesis fuzzing against a live container with no PRIVATE_KEY
    configured (exactly CI's actual environment), not by inspection."""
    try:
        return get_bridge()
    except Exception as e:
        logger.error("v1_bridge_unavailable", error=str(e))
        raise ApiError(503, "V1 blockchain bridge unavailable — see server logs for details", ErrorCode.BRIDGE_UNAVAILABLE)


# ─────────────────────────────────────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    # Bounded to stop an unbounded task string from becoming an
    # unbounded LLM cost/DoS vector — every agent in the pipeline sees
    # this same string, so its length multiplies across 4 LLM calls,
    # not just one. 10,000 chars (~2,500 tokens for English prose) is
    # generous for a real task description while still bounding the
    # worst case; a legitimate caller needing more should split the
    # work, not send one unbounded prompt.
    task:   str = Field(..., min_length=1, max_length=10_000)
    run_id: Optional[str] = None


class RunAgentResponse(BaseModel):
    run_id:     str
    task:       str
    status:     str = "started"
    stream_url: str


class RegisterAgentRequest(BaseModel):
    """Matches the SDK's tc.register_agent(agent_id, model, version,
    system_prompt=...) call (plan §13.2) — code_hash is computed
    CLIENT-SIDE from {agentId, model, version, systemPrompt} (same
    keccak256(json.dumps(config, sort_keys=True)) scheme
    blockchain/hashing_utils.py already uses for the pipeline's own 4
    agents), never the raw system prompt itself. The backend only ever
    sees and stores the hash."""
    agent_id:   str = Field(min_length=1, max_length=100)
    code_hash:  str = Field(min_length=66, max_length=66, pattern=r"^0x[0-9a-fA-F]{64}$")
    model:      str = Field(min_length=1, max_length=200)
    version:    str = Field(min_length=1, max_length=100)


class RegisterAgentResponse(BaseModel):
    agent_id: str
    tx_hash:  str


class LogStepRequest(BaseModel):
    """Matches the SDK's tc.log(agent_id=..., action=..., input=...,
    output=...) call — SDK ingest of a THIRD-PARTY agent's own step
    (plan §7.4/§13.4), as opposed to POST /run-agent, which runs
    TrustChain's own 4-agent pipeline. `run_id` is picked by the caller
    (their own logical grouping, e.g. one per agent invocation) — there's
    no separate "create a run" call in this workflow; the first step
    logged under a new run_id creates it."""
    run_id:      str = Field(min_length=1, max_length=64)
    agent_id:    str = Field(min_length=1, max_length=100)
    action:      str = Field(min_length=1, max_length=100)
    input:       str = Field(max_length=100_000)
    output:      str = Field(max_length=100_000)
    trust_score: int = Field(default=0, ge=0, le=100)
    # Phase 3 §6.2 — the SDK's own fingerprint of the agent that produced
    # this step, computed the same way register_agent()'s code_hash is.
    # Optional: an older SDK that doesn't send this gets leaf schema v1
    # (agents/base.py::log_step) and no drift check, exactly Phase 2
    # behavior — nothing breaks for a caller that hasn't upgraded.
    agent_code_hash: Optional[str] = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")


class LogStepResponse(BaseModel):
    step_id:       int
    outbox_id:     int
    status:        str = "queued"
    anchor_status: str


class VerifyRequest(BaseModel):
    runId: str   # FIX 2: was agent_id + code_hash_hex — but frontend sends
                 # { runId } per the locked VerifyRequest type in types.ts.
                 # Backend must accept runId and verify all 4 agents itself.


class VerifyContentRequest(BaseModel):
    """POST /integrity/verify-content's body — see that endpoint's own
    docstring. `field` as Literal, not a manual runtime check, so an
    invalid value is a standard 422 for free."""
    stepId:        int
    field:         Literal["input", "output"]
    candidateText: str = Field(min_length=1, max_length=100_000)


class SignupRequest(BaseModel):
    name:     str = Field(min_length=1, max_length=100)
    email:    EmailStr
    password: str = Field(min_length=8, max_length=200)
    # Phase 3 §4.1 — all three optional so the deployed frontend's existing
    # 3-field signup request keeps working completely unchanged. Omitted
    # org_name/project_name fall back to the original "<Name>'s
    # Organization"/"Default" defaults verbatim. invite_token routes
    # signup through the invitation path (§5.3) instead of provisioning a
    # personal org — org_name/project_name are ignored when it's given.
    org_name:     Optional[str] = Field(default=None, min_length=1, max_length=200)
    project_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    invite_token: Optional[str] = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    name:  str
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class TokenPairResponse(BaseModel):
    access_token:  str
    refresh_token: str
    expires_in:    int


class CreateApiKeyRequest(BaseModel):
    scopes:      list[str]
    environment: str = "live"


class ApiKeyCreatedResponse(BaseModel):
    id:        int
    raw_key:   str   # shown exactly once — never returned by any other endpoint
    last_four: str
    scopes:    list[str]


class ApiKeyListItem(BaseModel):
    id:            int
    last_four:     str
    scopes:        list[str]
    created_at:    int
    expires_at:    Optional[int]
    revoked_at:    Optional[int]
    last_used_at:  Optional[int]


# ─────────────────────────────────────────────────────────────────────────────
#  POST /auth/signup, POST /auth/login
#
#  Response shape and the primary token's lifetime are UNCHANGED from
#  Phase 1/2.0-2.2 — the deployed frontend depends on both. What changed is
#  invisible to it: the token now embeds project_id/org_id (auth.py), and
#  signup provisions a real Organization/Project underneath (db.tenancy).
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/signup", response_model=AuthResponse)
async def signup(body: SignupRequest, request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "signup", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    if settings.check_pwned_passwords:
        if await auth_pwned.is_password_pwned(body.password, settings.pwned_passwords_timeout_seconds):
            observability.SIGNUP_PWNED_PASSWORD_REJECTIONS_TOTAL.inc()
            raise ApiError(
                400,
                "this password has appeared in a known data breach — please choose a different one",
                ErrorCode.PASSWORD_PWNED,
            )

    invitation = None
    if body.invite_token:
        # Raises 404/400 directly (INVITATION_NOT_FOUND / EMAIL_MISMATCH)
        # if the token is invalid, expired, revoked, already used, or
        # addressed to a different email than this signup — Phase 3 §5.3.
        invitation = await invitations_db.validate_for_signup(body.invite_token, body.email, int(time.time()))

    try:
        user = await db.create_user(
            email=body.email, name=body.name, password=body.password,
            created_at=int(time.time()),
            org_name=body.org_name, project_name=body.project_name,
            invitation=invitation,
        )
    except ValueError:
        raise ApiError(409, "email already registered", ErrorCode.EMAIL_ALREADY_REGISTERED)

    if invitation is not None:
        accepted = await invitations_db.mark_accepted(invitation["invitation_id"], user["userId"], int(time.time()))
        if not accepted:
            # Lost the single-use race — extremely unlikely (this token was
            # just validated above), but the user row and membership are
            # already committed at this point, so the honest outcome is
            # "signed up successfully, into an org via a token that was
            # simultaneously consumed elsewhere" rather than a rollback
            # that would orphan a real user account.
            logger.warning("invitation_race_lost_after_signup", invitation_id=invitation["invitation_id"])
        observability.INVITATIONS_TOTAL.labels(action="accepted").inc()

    # Phase 4 G1 — every new account starts unverified EXCEPT one signing
    # up via a valid invitation (db.create_user already set
    # email_verified=True for that case — see its own docstring for why).
    # Send the verification email inline, same best-effort discipline as
    # the invitation email above (a failed send here doesn't lose data,
    # the token row already exists, and POST /auth/resend-verification is
    # the retry path). Must never fail signup itself.
    if invitation is None:
        from notifications.verification import queue_verification_email

        verification_settings = get_settings()
        verification = await email_verification_db.create_verification_token(
            user["userId"], int(time.time()), verification_settings.email_verification_ttl_seconds,
        )
        await queue_verification_email(
            user_id=user["userId"], email=user["email"], name=user["name"],
            raw_token=verification["rawToken"], ttl_seconds=verification_settings.email_verification_ttl_seconds,
        )

    token = auth.create_token(
        email=user["email"], name=user["name"],
        project_id=user["projectId"], org_id=user["orgId"], user_id=user["userId"],
    )
    return AuthResponse(token=token, name=user["name"], email=user["email"])


@router.post("/auth/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request):
    """Credential-stuffing defense (plan §11.3): exponential backoff per
    account AND per IP on failed attempts, checked BEFORE the password
    verification runs — a locked-out attempt shouldn't pay for PBKDF2."""
    client_ip = rate_limit.get_client_ip(request)
    await rate_limit.check_login_backoff(body.email, client_ip)

    user = await db.authenticate_user(email=body.email, password=body.password)
    if user is None:
        await rate_limit.record_login_failure(body.email, client_ip)
        raise ApiError(401, "invalid email or password", ErrorCode.INVALID_CREDENTIALS)

    await rate_limit.clear_login_failures(body.email, client_ip)
    token = auth.create_token(
        email=user["email"], name=user["name"],
        project_id=user["projectId"], org_id=user["orgId"], user_id=user["userId"],
    )
    return AuthResponse(token=token, name=user["name"], email=user["email"])


# ─────────────────────────────────────────────────────────────────────────────
#  POST /auth/token-pair, POST /auth/refresh, POST /auth/logout
#
#  Additive short-lived-access + rotating-refresh flow (plan §11.3) — for
#  the SDK/CLI and any future frontend, not the current web login (see
#  auth.py's module docstring on why /auth/login stays as it is).
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/token-pair", response_model=TokenPairResponse)
async def issue_token_pair(current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    """Exchanges a valid primary-session JWT for a short-lived access
    token + rotating refresh token — the on-ramp to the additive flow
    without requiring a second login."""
    pair = await refresh.issue_token_pair(
        current_user.user_id, current_user.email, current_user.name,
        current_user.project_id, current_user.org_id,
    )
    return TokenPairResponse(
        access_token=pair["accessToken"], refresh_token=pair["refreshToken"], expires_in=pair["expiresIn"]
    )


@router.post("/auth/refresh", response_model=TokenPairResponse)
async def refresh_token_pair(body: RefreshRequest):
    try:
        pair = await refresh.rotate_refresh_token(body.refresh_token)
    except refresh.RefreshError as e:
        logger.warning("refresh_token_rejected", reason=str(e))
        raise ApiError(401, "invalid, expired, or reused refresh token", ErrorCode.INVALID_REFRESH_TOKEN)
    return TokenPairResponse(
        access_token=pair["accessToken"], refresh_token=pair["refreshToken"], expires_in=pair["expiresIn"]
    )


@router.post("/auth/logout")
async def logout(body: RefreshRequest):
    await refresh.revoke_family_for_token(body.refresh_token)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  POST /auth/resend-verification, POST /auth/verify-email/{token}
#
#  Phase 4 G1 — see permissions.py's REQUIRES_VERIFIED_EMAIL for what an
#  unverified account is blocked from doing until it goes through this.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/resend-verification")
async def resend_verification(request: Request, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "resend-verification", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    user_row = await email_verification_db.get_user_for_resend(current_user.user_id)
    if user_row is None:
        # The JWT decoded fine but the user row is gone — shouldn't happen
        # outside a hand-crafted token or a race with account deletion;
        # fail the same way a truly-invalid credential would rather than
        # a 500 for something that isn't a server bug.
        raise ApiError(401, "invalid or expired token", ErrorCode.INVALID_TOKEN)
    if user_row["emailVerified"]:
        # Idempotent no-op, not an error — a stale "resend" click after
        # already verifying (e.g. via a different tab) shouldn't surface
        # as a failure to the caller.
        return {"ok": True, "alreadyVerified": True}

    from notifications.verification import queue_verification_email

    verification = await email_verification_db.create_verification_token(
        current_user.user_id, int(time.time()), settings.email_verification_ttl_seconds,
    )
    await queue_verification_email(
        user_id=current_user.user_id, email=user_row["email"], name=user_row["name"],
        raw_token=verification["rawToken"], ttl_seconds=settings.email_verification_ttl_seconds,
    )
    return {"ok": True, "alreadyVerified": False}


@router.post("/auth/verify-email/{token}")
async def verify_email(token: str, request: Request):
    """Unauthenticated by design, same reasoning as GET /invitations/{token}
    — the link is meant to work from wherever the recipient opens their
    email, not only from the browser tab they signed up in."""
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "verify-email", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    user_id = await email_verification_db.consume_token(token, int(time.time()))
    if user_id is None:
        raise ApiError(400, "verification link is invalid, expired, or already used", ErrorCode.VERIFICATION_TOKEN_INVALID)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  POST /auth/forgot-password, POST /auth/reset-password/{token}
#
#  Phase 4 G2. The forgot-password response is IDENTICAL whether or not
#  the account exists (Phase 4 plan §3 step 3) — db.password_reset's
#  create_reset_token_if_exists, not this handler, is where "does this
#  account exist" is ever branched on, so that property can't be broken
#  by a future edit to this endpoint alone.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "forgot-password", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    reset = await password_reset_db.create_reset_token_if_exists(
        body.email, int(time.time()), settings.password_reset_ttl_seconds,
    )
    if reset is not None:
        from notifications.password_reset import queue_password_reset_email

        await queue_password_reset_email(
            user_id=reset["userId"], email=reset["email"], name=reset["name"],
            raw_token=reset["rawToken"], ttl_seconds=settings.password_reset_ttl_seconds,
        )
    # Same {"ok": true} either way — see the section docstring above.
    return {"ok": True}


@router.post("/auth/reset-password/{token}")
async def reset_password_endpoint(token: str, body: ResetPasswordRequest, request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "reset-password", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    if settings.check_pwned_passwords:
        if await auth_pwned.is_password_pwned(body.new_password, settings.pwned_passwords_timeout_seconds):
            raise ApiError(
                400,
                "this password has appeared in a known data breach — please choose a different one",
                ErrorCode.PASSWORD_PWNED,
            )

    user_id = await password_reset_db.reset_password(token, body.new_password, int(time.time()))
    if user_id is None:
        raise ApiError(400, "reset link is invalid, expired, or already used", ErrorCode.RESET_TOKEN_INVALID)

    # Every existing session dies with the old password — see
    # db/password_reset.py's module docstring for why this lives here
    # rather than inside reset_password itself.
    await refresh.revoke_all_for_user(user_id)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  API key management — human/JWT-only (never an API key itself), and
#  restricted to owner/admin members of the key's org (plan §14.2: members
#  can use a project, only owner/admin can mint credentials for it).
# ─────────────────────────────────────────────────────────────────────────────

async def _require_admin(current_user: auth.CurrentUser) -> None:
    """Thin wrapper kept for this section's existing call sites — Phase 3
    generalized the underlying check into permissions.require_permission
    (backend/permissions.py), which every new Phase 3 endpoint calls
    directly with a specific Permission rather than this API-key-era
    admin-or-nothing helper. Behavior for THESE THREE routes (API key
    create/list/revoke) is unchanged: owner or admin, same as before."""
    await permissions.require_permission(current_user.user_id, current_user.org_id, permissions.Permission.APIKEY_CREATE)


@router.post("/api-keys", response_model=ApiKeyCreatedResponse)
@v1_only_router.post("/keys", response_model=ApiKeyCreatedResponse)  # Appendix A: POST /v1/keys
async def create_api_key(body: CreateApiKeyRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    try:
        key = await tenancy.create_api_key(
            current_user.project_id, body.scopes, int(time.time()), environment=body.environment,
        )
    except ValueError as e:
        raise ApiError(400, str(e), ErrorCode.API_KEY_CREATE_FAILED)

    await audit_log_admin_action(current_user, "api_key.created", target=f"api_key:{key['id']}")
    return ApiKeyCreatedResponse(id=key["id"], raw_key=key["rawKey"], last_four=key["lastFour"], scopes=key["scopes"])


@router.get("/api-keys")
async def list_api_keys(current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        current_user.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    keys = await tenancy.list_api_keys(current_user.project_id)
    return {"keys": keys}


@router.delete("/api-keys/{key_id}")
@v1_only_router.delete("/keys/{key_id}")  # Appendix A: DELETE /v1/keys/{id}
async def revoke_api_key(key_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    revoked = await tenancy.revoke_api_key(key_id, current_user.project_id, int(time.time()))
    if not revoked:
        raise ApiError(404, "API key not found (or already revoked)", ErrorCode.API_KEY_NOT_FOUND)
    await audit_log_admin_action(current_user, "api_key.revoked", target=f"api_key:{key_id}")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Background pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_pipeline_background(task: str, run_id: str, org_id: int):
    """
    Persists the run's terminal DB status (db.complete_run/fail_run) and
    the PIPELINE_RUNS_TOTAL metric based on which event run_pipeline()
    actually yielded — NOT on whether iterating it raised an exception.

    agents/pipeline.py's run_pipeline() catches its OWN internal
    exceptions (any LangGraph node failing — an LLM call, a tool call,
    etc.) and yields a normal {"type": "error", ...} event instead of
    letting the exception propagate; the generator then returns normally.
    An earlier version of this function assumed "the async-for loop
    finished without raising" meant success, calling db.complete_run()
    only on an explicit "run_complete" event but otherwise falling
    through to a blanket "completed" metric with NEITHER db.complete_run()
    nor db.fail_run() ever called for the "error" case — a run that
    failed this way stayed at status='running' in Postgres FOREVER (GET
    /runs/{run_id} 404s "not yet complete" indefinitely), while
    Prometheus recorded it as "completed". Caught via the Python SDK's
    integration tests polling GET /runs/{run_id} right after a real
    pipeline run failed on a real Groq rate limit — not a hypothetical.

    get_bridge() is called INSIDE the try block, not before it — a
    second real bug found via real CI (not local dev, which always has a
    real PRIVATE_KEY configured): blockchain/client.py's V1 bridge raises
    ValueError("PRIVATE_KEY not set in .env") whenever that secret isn't
    configured, which is exactly GitHub Actions' backend test job (it
    correctly doesn't fabricate a real Monad testnet deployer key as a
    secret). With get_bridge() unprotected before the try, that raise
    killed this whole background task silently — before a single event
    was ever published, before db.fail_run() ever ran — leaving the run
    stuck at status='running' forever and the SSE stream's only signal a
    120-second client-side timeout. Treating bridge-init failure as just
    another pipeline failure (same error-event + db.fail_run() path any
    other exception already gets) is both more correct AND is what
    actually made the bug visible instead of silently hanging.
    """
    bind_run_id(run_id)
    observability.PIPELINE_RUNS_TOTAL.labels(status="started").inc()
    start = time.monotonic()
    tracer = observability.get_tracer(__name__)
    succeeded = False
    try:
        bridge = get_bridge()
        with tracer.start_as_current_span("pipeline_run") as span:
            span.set_attribute("run_id", run_id)
            async for event in run_pipeline(task, run_id=run_id, bridge=bridge):
                await run_events.publish_event(run_id, event)
                if event.get("type") == "run_complete":
                    await db.complete_run(run_id, event, int(time.time()))
                    succeeded = True
                    tokens_used = event.get("tokensUsed", 0)
                    if tokens_used:
                        await tenancy.record_token_spend(org_id, tokens_used)
                        observability.LLM_TOKENS_USED_TOTAL.labels(org_id=str(org_id)).inc(tokens_used)
                elif event.get("type") == "error":
                    await db.fail_run(run_id, event.get("message", "pipeline error"), int(time.time()))
        observability.PIPELINE_RUNS_TOTAL.labels(status="completed" if succeeded else "failed").inc()
    except Exception as e:
        # A DIFFERENT failure mode from the one above: something raised
        # that run_pipeline() itself did NOT catch (e.g. a bug in this
        # function, or in run_events/db plumbing) — still needs the same
        # fail_run()/error-event/metric handling, just via this path
        # instead.
        logger.error("pipeline_background_error", error=str(e))
        observability.PIPELINE_RUNS_TOTAL.labels(status="failed").inc()
        await run_events.publish_event(run_id, {"type": "error", "runId": run_id, "message": str(e)})
        await db.fail_run(run_id, str(e), int(time.time()))
    finally:
        observability.PIPELINE_RUN_DURATION_SECONDS.observe(time.monotonic() - start)
        await run_events.publish_terminal(run_id)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /run-agent
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/run-agent", response_model=RunAgentResponse)
@v1_only_router.post("/runs", response_model=RunAgentResponse)  # Appendix A: POST /v1/runs
async def run_agent(
    body: RunAgentRequest,
    principal: auth.Principal = Depends(auth.get_current_principal),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Accepts either a human JWT or an API key with the `runs:write`
    scope — an SDK-driven third-party agent never needs a human login to
    start a run. Rate-limited and quota-checked per plan §11.4 (this
    endpoint spends both LLM credits and gas — real money), and honors an
    optional Idempotency-Key so a client's retried request after a
    dropped response doesn't start the pipeline twice."""
    auth.require_scope(principal, "runs:write")
    if not body.task.strip():
        raise ApiError(400, "task cannot be empty", ErrorCode.TASK_EMPTY)

    request_body_bytes = body.model_dump_json().encode()

    if idempotency_key:
        try:
            cached = await idempotency.get_cached_response(
                principal.project_id, idempotency_key, "POST", "/run-agent", request_body_bytes,
            )
        except idempotency.IdempotencyConflict as e:
            raise ApiError(409, str(e), ErrorCode.IDEMPOTENCY_CONFLICT)
        if cached is not None:
            return cached["body"]

    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:run-agent:{principal.project_id}",
        settings.run_agent_rate_limit_capacity,
        settings.run_agent_rate_limit_refill_per_second,
    )

    window_start = int(time.time()) - 30 * 24 * 3600
    run_count = await tenancy.count_org_runs_in_window(principal.org_id, window_start)
    if run_count >= settings.monthly_run_quota_per_org:
        raise ApiError(429, "monthly run quota exceeded for this organization", ErrorCode.QUOTA_EXCEEDED)

    # Aggregate LLM token budget (plan O10) — same shape as the gas-spend
    # ceiling (GET /gas-spend's orgGasBudget): a run never even starts
    # once an org's real cumulative token spend (organizations.tokens_spent,
    # updated after each run completes — see _run_pipeline_background)
    # reaches its configured organizations.token_budget. Distinct from
    # the per-run cap enforced mid-pipeline by agents/base.py's
    # track_token_usage, which guards against one run looping
    # pathologically rather than against total spend across many runs.
    token_budget_status = await tenancy.get_org_token_budget_status(principal.org_id)
    if token_budget_status["breached"]:
        observability.TOKEN_BUDGET_REJECTIONS_TOTAL.labels(org_id=str(principal.org_id)).inc()
        raise ApiError(429, "organization's LLM token budget exceeded", ErrorCode.TOKEN_BUDGET_EXCEEDED)

    run_id = body.run_id or make_run_id()
    user_email = principal.actor if not principal.is_api_key else None
    await db.create_run(run_id, principal.project_id, body.task, user_email, int(time.time()))
    _spawn_background_pipeline_run(body.task, run_id, principal.org_id)
    logger.info("run_started", run_id=run_id, actor=principal.actor, project_id=principal.project_id, task=body.task)

    response = RunAgentResponse(run_id=run_id, task=body.task, status="started", stream_url=f"/stream/{run_id}")

    if idempotency_key:
        await idempotency.store_response(
            principal.project_id, idempotency_key, "POST", "/run-agent", request_body_bytes,
            200, response.model_dump(), int(time.time()),
        )

    return response


# ─────────────────────────────────────────────────────────────────────────────
#  GET /stream/{run_id}  — SSE, backed by Redis Streams (run_events.py)
#
#  Deliberately unauthenticated, unlike every other endpoint below: browser
#  EventSource cannot attach an Authorization header, so the frontend
#  connects here with nothing but the run_id from POST /run-agent's
#  response. This predates multi-tenancy and stays as-is (a real fix needs
#  a short-lived signed stream token the frontend would have to adopt —
#  out of scope for this backend-only pass). It leaks no listing, only the
#  live event stream for one specific already-known run_id.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stream/{run_id}")
@v1_only_router.get("/runs/{run_id}/stream")  # Appendix A: GET /v1/runs/{id}/stream
async def stream_events(run_id: str, request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "stream", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )

    async def event_generator():
        try:
            async for event in run_events.read_events(run_id, timeout_seconds=120):
                yield f"data: {json.dumps(event)}\n\n"
            # Stream ended via the terminal marker (run_events.publish_terminal)
            # — was yielding a plain "data: [DONE]\n\n" string before Phase 1's
            # fix; useAgentStream's generator checks parsed.type ===
            # "run_complete" to detect end-of-stream, and a non-JSON [DONE]
            # fails that parse and throws instead of closing cleanly.
            yield f"data: {json.dumps({'type': 'run_complete', 'runId': run_id})}\n\n"
        except TimeoutError:
            logger.warning("sse_stream_timeout", run_id=run_id)
            yield f"data: {json.dumps({'type': 'error', 'runId': run_id, 'message': 'stream timeout'})}\n\n"
        except asyncio.CancelledError:
            logger.info("sse_client_disconnected", run_id=run_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            # FIX 4: do NOT set Access-Control-Allow-Origin here manually.
            # When CORSMiddleware is active, setting this header in the response
            # too causes a "multiple values" CORS error in the browser.
            # CORSMiddleware handles it — remove the duplicate.
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /audit-log — read model (steps + anchor_batches), not a live V1 call.
#  See db/read_model.py's module docstring for what stays on the live V1
#  bridge instead (/verify, /verify/tamper-demo, /verify-audit) and why.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    run_id: Optional[str] = Query(None),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    """Returns anchored-audit entries from Postgres — populated by the
    outbox (agents/base.py::log_step) and kept current by the anchor
    worker + indexer, not fetched from the chain on every request.
    Scoped to the caller's project (invariant I7) — a run_id belonging to
    another project returns an empty result, same as one that never
    existed at all."""
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        entries = await read_model.get_audit_log_entries(principal.project_id, run_id=run_id)
        return {"entries": entries, "total": len(entries)}
    except Exception as e:
        logger.error("api_error", endpoint="audit-log", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trust-scores")
async def get_trust_scores(
    run_id: str = Query(..., description="Run ID to fetch scores for"),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        scores = await read_model.get_trust_scores(principal.project_id, run_id)
        return {"runId": run_id, "scores": scores}
    except Exception as e:
        logger.error("api_error", endpoint="trust-scores", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores/history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trust-scores/history")
async def get_trust_score_history(
    run_id: str = Query(..., description="Run ID to fetch score history for"),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        history = await read_model.get_trust_score_history(principal.project_id, run_id)
        return {"runId": run_id, "history": history}
    except Exception as e:
        logger.error("api_error", endpoint="trust-scores/history", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /leaderboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/leaderboard")
async def get_leaderboard(
    max_runs: int = Query(50, ge=1, le=500),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    """
    Aggregates per-agent scores across recent runs IN THE CALLER'S PROJECT
    from the read model. If totalRuns > runsConsidered, only the most
    recent `max_runs` runs were aggregated — surfaced explicitly so the UI
    never silently under-reports. Prior to multi-tenancy this aggregated
    every run system-wide, which — now that there's more than one tenant
    — would itself be an invariant-I7 violation, not a feature to preserve.
    """
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        return await read_model.get_leaderboard(principal.project_id, max_runs=max_runs)
    except Exception as e:
        logger.error("api_error", endpoint="leaderboard", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /gas-spend — real gas-spend attribution (plan §11.4/O10)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/gas-spend")
async def get_gas_spend(principal: auth.Principal = Depends(auth.get_current_principal)):
    """Real cumulative wei spent anchoring this project's own steps —
    read straight off each confirming batch's own transaction receipt
    (anchor_worker/submit.py), not estimated. See db/read_model.py's
    get_gas_spend_summary for why one project can never see another's
    (invariant I7) or double-count a batch across the many steps it
    anchors.

    Also surfaces the caller's ORG-level gas budget/ceiling (plan
    §11.4's hard gas-spend circuit breaker, tenancy.get_org_gas_budget_status)
    and LLM token budget/ceiling (plan O10, tenancy.get_org_token_budget_status)
    alongside the project-level actual-spend figure above — different
    scopes (org vs. this one project) and different mechanisms (each an
    accumulated running counter updated when its own real cost is known —
    a confirmed anchor batch's receipt for gas, a completed run's
    usage_metadata for tokens — vs. a live query here), but all real, all
    answering "how close am I to being cut off" from the angles that
    matter."""
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        summary = await read_model.get_gas_spend_summary(principal.project_id)
        summary["orgGasBudget"] = await tenancy.get_org_gas_budget_status(principal.org_id)
        summary["orgTokenBudget"] = await tenancy.get_org_token_budget_status(principal.org_id)
        return summary
    except Exception as e:
        logger.error("api_error", endpoint="gas-spend", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /verify
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/verify")
async def verify_integrity(body: VerifyRequest, request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "verify", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    bridge = get_bridge_or_503()
    try:
        result = await bridge.verify_run(body.runId)  # already async
        return result
    except Exception as e:
        logger.error("api_error", endpoint="verify", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


@router.get("/verify/tamper-demo")
async def verify_tamper_demo(request: Request, agent_id: str = Query(..., description="e.g. researcher")):
    """
    Read-only proof that AgentIdentityRegistry actually detects substitution:
    compares the real agent config hash (should PASS) against a deliberately
    mutated one (should FAIL). No on-chain writes, no gas spent.
    """
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "verify_tamper_demo", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    bridge = get_bridge_or_503()
    try:
        return await bridge.tamper_demo(agent_id)
    except ValueError as e:
        raise ApiError(404, str(e), ErrorCode.TAMPER_DEMO_AGENT_NOT_FOUND)
    except Exception as e:
        logger.error("api_error", endpoint="tamper-demo", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


@router.get("/verify-audit")
async def verify_audit(request: Request, run_id: str = Query(...)):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "verify_audit", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    bridge = get_bridge_or_503()
    tracer = observability.get_tracer(__name__)
    try:
        with tracer.start_as_current_span("rpc_call.verify_audit") as span:
            span.set_attribute("run_id", run_id)

            # Step 1: get indices for this specific run
            indices = await asyncio.to_thread(
                bridge.audit_log.functions.getRunRecordIndices(run_id).call
            )
            logger.info("verify_audit_indices_fetched", run_id=run_id, count=len(indices))

            if not indices:
                span.set_attribute("entry_count", 0)
                return {"runId": run_id, "allMatch": True, "entries": []}

            # Step 2: batch fetch all records in one RPC call
            raw_records = await asyncio.to_thread(
                bridge.audit_log.functions.getRecordsBatch(indices).call
            )

            # Step 3: for each record call verifyRecord(index, rawInput, rawOutput)
            # BUT we don't have rawInput/rawOutput — only hashes are stored.
            # So instead: re-read each record twice and confirm consistency.
            results = []
            for idx, raw in zip(indices, raw_records):
                # Second independent read to confirm stability
                recheck = await asyncio.to_thread(
                    bridge.audit_log.functions.getRecord(idx).call
                )
                action_match = raw[2]      == recheck[2]       # action string
                input_match  = raw[3].hex() == recheck[3].hex() # inputHash bytes32
                output_match = raw[4].hex() == recheck[4].hex() # outputHash bytes32

                results.append({
                    "entryId":     idx,
                    "agentId":     raw[1],
                    "action":      raw[2],
                    "actionMatch": action_match,
                    "inputMatch":  input_match,
                    "outputMatch": output_match,
                    "txHash":      None,  # not in struct, enriched separately
                })

            all_match = all(
                r["actionMatch"] and r["inputMatch"] and r["outputMatch"]
                for r in results
            )
            span.set_attribute("entry_count", len(results))
            span.set_attribute("all_match", all_match)
            return {"runId": run_id, "allMatch": all_match, "entries": results}

    except Exception as e:
        logger.error("api_error", endpoint="verify-audit", error=str(e))
        raise ApiError(500, "internal error — see server logs for details", ErrorCode.INTERNAL_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /chain-status   — FIX 6: was using asyncio.to_thread for sync w3 calls
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/chain-status")
async def chain_status(request: Request):
    """
    Called by the frontend on mount to show the top status bar.
    DISCONNECTED in the UI means this endpoint is failing or unreachable.
    Most common causes: backend not running, CORS blocked, bridge error.
    """
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "chain_status", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    rpc_url = settings.monad_rpc_url
    try:
        bridge = get_bridge()
        # FIX 6: bridge.w3.eth.block_number and chain_id are SYNCHRONOUS.
        # Calling them directly in an async function blocks the uvicorn
        # event loop — other requests hang until it returns.
        # Wrap in asyncio.to_thread so they run in a thread pool instead.
        block_number = await asyncio.to_thread(lambda: bridge.w3.eth.block_number)
        chain_id     = await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
        return {
            "connected":         True,
            "chainId":           chain_id,
            "blockNumber":       block_number,
            "rpcUrl":            rpc_url,
            "contractsDeployed": 3,
        }
    except Exception as e:
        logger.error("api_error", endpoint="chain-status", error=str(e))
        # Return disconnected shape — never 500 on this endpoint.
        # Frontend handles connected: false gracefully.
        return {
            "connected":         False,
            "chainId":           0,
            "blockNumber":       0,
            "rpcUrl":            rpc_url,
            "contractsDeployed": 3,
            "error":             str(e),   # shows in uvicorn log, not UI
        }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Phase 4 G7: PRIVATE_KEY simply not being set (the normal state for
    CI, for any environment that only runs V2, and for local dev unless a
    real testnet key was deliberately added) is NOT the same condition as
    the V1 bridge failing to connect/authenticate with a key it DOES have
    — the first is an expected, documented state; the second is a real
    operational problem. Both used to collapse into the identical 503
    BRIDGE_UNAVAILABLE, which trains whoever's watching this endpoint to
    treat 503-from-/health as routine noise — exactly the failure mode
    that makes a REAL bridge outage easy to miss.

    Distinguished by catching get_bridge() directly (not via
    get_bridge_or_503, which already collapses every failure into one
    503) and checking for blockchain.client.BlockchainBridge's own exact
    "PRIVATE_KEY not set in .env" ValueError — everything else construction
    can raise (RPC unreachable, bad key format, contract load failure)
    still gets the real 503 below, unchanged. Calling get_bridge() (not
    constructing BlockchainBridge directly) is also what keeps
    tests/conftest.py's client_with_fake_bridge fixture (which
    monkeypatches main.get_bridge itself) working unaffected — that path
    never raises at all, so it never reaches either branch here."""
    try:
        bridge = get_bridge()
    except ValueError as e:
        if str(e) == "PRIVATE_KEY not set in .env":
            return {"status": "not_configured", "reason": str(e)}
        logger.error("v1_bridge_unavailable", error=str(e))
        raise ApiError(503, "V1 blockchain bridge unavailable — see server logs for details", ErrorCode.BRIDGE_UNAVAILABLE)
    except Exception as e:
        logger.error("v1_bridge_unavailable", error=str(e))
        raise ApiError(503, "V1 blockchain bridge unavailable — see server logs for details", ErrorCode.BRIDGE_UNAVAILABLE)

    try:
        chain_id = await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
    except Exception as e:
        logger.error("v1_bridge_unavailable", error=str(e))
        raise ApiError(503, "V1 blockchain bridge unavailable — see server logs for details", ErrorCode.BRIDGE_UNAVAILABLE)
    return {
        "status":   "ok",
        "chain_id": chain_id,
        "wallet":   bridge.account.address,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /ready  — readiness probe: are this process's real dependencies up?
#
#  Distinct from /health, which is a liveness-flavored check that also
#  happens to touch the chain. /ready is what an orchestrator should gate
#  traffic on: it checks the database (the one dependency every request
#  path actually needs), that the database's applied Alembic migration
#  matches what this checked-out code's alembic/versions/ expects (F15 —
#  a running process whose schema is behind isn't safe to route real
#  traffic to; a fresh test/dev DB with no alembic_version table at all
#  is a different, non-blocking case — see db.get_applied_migration_version's
#  docstring), and reports chain reachability as informational, not
#  blocking — a chain outage degrades specific endpoints, it shouldn't
#  take the whole process out of rotation, matching how the rest of this
#  file already treats bridge failures as non-fatal.
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ready")
async def ready():
    """No auth on this endpoint (an orchestrator's health-checker doesn't
    carry a bearer token) — so a failing check's raw exception (a Postgres
    DSN with credentials in a connection error, an RPC URL with an
    embedded API key in a chain-connectivity error) must never reach the
    response body. Logged server-side in full instead; the caller gets
    only a boolean per check."""
    checks: dict[str, dict] = {}
    ok = True

    try:
        await db.ping()
        checks["database"] = {"ok": True}
    except Exception as e:
        logger.error("readiness_check_failed", check="database", error=str(e))
        checks["database"] = {"ok": False}
        ok = False

    try:
        from db.engine import get_code_migration_head

        applied = await db.get_applied_migration_version()
        code_head = get_code_migration_head()
        if applied is None:
            # No alembic_version table at all — the schema was built
            # straight from the ORM models (tests, or a dev DB before its
            # first `alembic upgrade head`), not something a real
            # deployment (which always migrates before starting the app)
            # does. Informational: this check simply doesn't apply here,
            # not a signal anything is actually wrong.
            checks["migrations"] = {"ok": True}
        elif applied == code_head:
            checks["migrations"] = {"ok": True}
        else:
            logger.error("readiness_check_failed", check="migrations", applied=applied, expected=code_head)
            checks["migrations"] = {"ok": False}
            ok = False
    except Exception as e:
        logger.error("readiness_check_failed", check="migrations", error=str(e))
        checks["migrations"] = {"ok": False}
        ok = False

    try:
        bridge = get_bridge()
        await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
        checks["chain"] = {"ok": True}
    except Exception as e:
        logger.error("readiness_check_failed", check="chain", error=str(e))
        checks["chain"] = {"ok": False}
        # informational only — does not flip overall readiness

    return {"ready": ok, "checks": checks}


# ─────────────────────────────────────────────────────────────────────────────
#  GET /runs, GET /runs/{run_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    """Run history, scoped to the caller's project (invariant I7) —
    survives backend restarts (persisted in Postgres)."""
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    runs = await db.list_runs(principal.project_id, limit=limit)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, principal: auth.Principal = Depends(auth.get_current_principal)):
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )

    # Always reads Postgres, scoped by project_id (invariant I7) — no
    # in-process cache. The old in-memory _run_results fast path had the
    # same cross-replica blind spot Redis Streams just fixed for SSE (a
    # second API replica never sees another replica's in-memory writes),
    # and db.complete_run/fail_run already commit synchronously right
    # when the terminal event is produced, so the "fast path" bought
    # negligible latency at the cost of that correctness gap.
    run = await db.get_run(run_id, principal.project_id)
    if run is None:
        raise ApiError(404, f"Run '{run_id}' not found", ErrorCode.RUN_NOT_FOUND)
    if run["status"] == "running":
        raise ApiError(404, f"Run '{run_id}' not yet complete", ErrorCode.RUN_NOT_COMPLETE)
    return run["result"] if run["result"] is not None else run


# ─────────────────────────────────────────────────────────────────────────────
#  POST /agents, GET /agents/{agent_id}/verify — SDK register_agent()/
#  verify_agent() (plan §7.3/§13.2). Distinct from the pipeline's own
#  internal identity checks (agents/base.py, blockchain/hashing_utils.py)
#  — this is the SAME on-chain registry (AgentIdentityRegistryV2), just
#  reachable by ANY authenticated project for agents THEY run themselves.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/agents", response_model=RegisterAgentResponse)
async def register_agent(
    body: RegisterAgentRequest,
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    auth.require_scope(principal, "agents:register")
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:register-agent:{principal.project_id}",
        settings.register_agent_rate_limit_capacity,
        settings.register_agent_rate_limit_refill_per_second,
        kind="register_agent",
    )
    try:
        tx_hash = await identity_writer.register_agent(
            principal.project_id, body.agent_id, body.code_hash, body.model, body.version,
        )
    except Exception as e:
        logger.error("api_error", endpoint="agents.register", error=str(e))
        raise ApiError(502, "on-chain registration failed — see server logs for details", ErrorCode.AGENT_REGISTRATION_FAILED)
    return RegisterAgentResponse(agent_id=body.agent_id, tx_hash=tx_hash)


@router.get("/agents")
async def list_agents(
    include_revoked: bool = Query(False),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    """Every agent registered under the caller's project — read model
    (db/read_model.py::list_agents over db/models.py's Agent table),
    not a live on-chain call, since listing needs to enumerate rather
    than check one known agent_id (the chain has no "list all agents
    for project X" view — AgentIdentityRegistryV2's registeredKeys is
    deliberately cross-tenant and untouched by the backend, see that
    contract's own comment). Revoked agents are excluded by default
    (?include_revoked=true to see them too) — a revoked agent_id can be
    re-registered fresh, so most callers listing "my agents" want the
    currently-usable set, not history."""
    auth.require_scope(principal, "agents:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    agents = await read_model.list_agents(principal.project_id, include_revoked=include_revoked)
    return {"agents": agents, "total": len(agents)}


@router.get("/agents/{agent_id}/verify")
async def verify_agent(
    agent_id: str,
    code_hash: str = Query(..., pattern=r"^0x[0-9a-fA-F]{64}$"),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    """Live re-verification (§7.3): recomputes nothing here — the caller
    hashes its OWN current config client-side (same scheme as
    registration) and asks whether that hash still matches what's
    registered and active on-chain. A mismatch means the agent's model,
    version, or prompt changed without re-registering — the same "silent
    substitution" signal AgentIdentityRegistryV2.verifyAgentFull was
    built to catch for the pipeline's own 4 agents, now reachable for
    anyone's."""
    auth.require_scope(principal, "agents:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    try:
        return await identity_writer.verify_agent(principal.project_id, agent_id, code_hash)
    except Exception as e:
        logger.error("api_error", endpoint="agents.verify", error=str(e))
        raise ApiError(502, "on-chain verification failed — see server logs for details", ErrorCode.AGENT_VERIFICATION_FAILED)


@router.get("/agents/{agent_id}/integrity")
async def get_agent_integrity(agent_id: str, principal: auth.Principal = Depends(auth.get_current_principal)):
    """Phase 3 §6.2/§6.3/§9.7 — the current-state summary a dashboard
    reads directly rather than re-aggregating rm_agent_events on every
    request: registered identity, last time it was seen matching (detector
    1, on the write path) or drifting, and recent identity events/alerts.
    """
    auth.require_scope(principal, "agents:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    from sqlalchemy import select
    from db.engine import get_sessionmaker
    from db.models import Agent, ReadModelAgentEvent

    async with get_sessionmaker()() as session:
        record = (await session.execute(
            select(Agent).where(Agent.project_id == principal.project_id, Agent.agent_id == agent_id)
        )).scalar_one_or_none()
        if record is None:
            raise ApiError(404, f"agent '{agent_id}' not found", ErrorCode.STEP_NOT_FOUND)

        events = (await session.execute(
            select(ReadModelAgentEvent)
            .where(ReadModelAgentEvent.project_id == principal.project_id, ReadModelAgentEvent.agent_id == agent_id)
            .order_by(ReadModelAgentEvent.timestamp.desc()).limit(20)
        )).scalars().all()

    project = await orgs_db.get_project(principal.project_id)
    open_alerts: list[dict] = []
    if project is not None:
        rows, _ = await alerts_db.list_alerts(
            project["orgId"], status="open", project_id=principal.project_id, limit=20,
        )
        open_alerts = [a for a in rows if a["subject"] == f"agent:{agent_id}"]

    return {
        "agentId": agent_id, "registeredCodeHash": record.code_hash, "model": record.model, "version": record.version,
        "isActive": record.is_active, "lastVerifiedAt": record.last_verified_at, "lastDriftAt": record.last_drift_at,
        "events": [
            {"eventType": e.event_type, "actor": e.actor, "codeHash": e.code_hash, "timestamp": e.timestamp,
             "txHash": e.tx_hash, "blockNumber": e.block_number}
            for e in events
        ],
        "openAlerts": open_alerts,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  POST /steps, GET /steps/{step_id}/proof — SDK ingest of a THIRD-PARTY
#  agent's own step, and the Merkle inclusion proof for it (plan §7.2/
#  §7.4/§13.4). This is the actual "any third-party agent can be audited"
#  path — POST /run-agent runs TrustChain's OWN pipeline; this lets a
#  caller audit an agent it runs itself, writing through the exact same
#  outbox + Merkle-batching machinery (agents/base.py::log_step) the
#  internal pipeline uses, so durability and anchoring work identically
#  either way.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/steps", response_model=LogStepResponse)
async def log_external_step(
    body: LogStepRequest,
    principal: auth.Principal = Depends(auth.get_current_principal),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    auth.require_scope(principal, "logs:write")
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:log-step:{principal.project_id}",
        settings.log_step_rate_limit_capacity,
        settings.log_step_rate_limit_refill_per_second,
        kind="log_step",
    )

    request_body_bytes = body.model_dump_json().encode()
    if idempotency_key:
        try:
            cached = await idempotency.get_cached_response(
                principal.project_id, idempotency_key, "POST", "/steps", request_body_bytes,
            )
        except idempotency.IdempotencyConflict as e:
            raise ApiError(409, str(e), ErrorCode.IDEMPOTENCY_CONFLICT)
        if cached is not None:
            return cached["body"]

    owned = await db.get_or_create_run_for_project(body.run_id, principal.project_id, int(time.time()))
    if not owned:
        raise ApiError(404, f"Run '{body.run_id}' not found", ErrorCode.RUN_NOT_FOUND)

    step_index = await db.next_step_index(body.run_id)
    _, event = await log_step(
        bridge=None,
        agent_id=body.agent_id,
        action=body.action,
        input_text=body.input,
        output_text=body.output,
        step_index=step_index,
        run_id=body.run_id,
        trust_score=body.trust_score,
        agent_code_hash=body.agent_code_hash,
        project_id=principal.project_id,
    )
    response = LogStepResponse(
        step_id=event["stepId"], outbox_id=event["outboxId"], anchor_status=event["anchorStatus"],
    )

    if idempotency_key:
        await idempotency.store_response(
            principal.project_id, idempotency_key, "POST", "/steps", request_body_bytes,
            200, response.model_dump(), int(time.time()),
        )

    return response


@router.get("/steps/{step_id}/proof")
async def get_step_proof(step_id: int, principal: auth.Principal = Depends(auth.get_current_principal)):
    auth.require_scope(principal, "runs:read")
    settings = get_settings()
    await rate_limit.enforce_read_rate_limit(
        principal.project_id, settings.read_path_rate_limit_capacity, settings.read_path_rate_limit_refill_per_second,
    )
    proof = await read_model.get_step_proof(step_id, principal.project_id)
    if proof is None:
        raise ApiError(
            404, f"Step {step_id} not found, not yours, or not yet anchored in a batch",
            ErrorCode.STEP_NOT_FOUND,
        )
    return proof


# ─────────────────────────────────────────────────────────────────────────────
#  GET /stats — public platform-level counters (plan Appendix A). No auth,
#  by design (see read_model.get_platform_stats's docstring for why it's
#  still safe: aggregate-only, never a per-tenant breakdown).
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def platform_stats(request: Request):
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "stats", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    return await read_model.get_platform_stats()


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — organizations, roles, invitations, alerts & continuous integrity
#
#  Everything below is additive: no existing route's request/response shape
#  changes. Human-only endpoints (org/project/member/invitation/alert
#  mutation, notification prefs) depend on auth.get_current_user (JWT only,
#  never an API key — same rule api-key management above already follows).
#  Read endpoints an SDK/API-key consumer can legitimately also want
#  (agents/{id}/integrity, steps/{id}/proof) stay on
#  auth.get_current_principal. See permissions.py for the full matrix this
#  enforces (Appendix A of the Phase 3 plan).
# ═════════════════════════════════════════════════════════════════════════════

async def audit_log_org_action(user_id: int, org_id: int, action: str, target: Optional[str] = None) -> None:
    """Like audit_log_admin_action above, but takes an explicit org_id —
    Phase 3 endpoints act on an org_id path parameter that may differ
    from current_user.org_id (their JWT's currently-active org), so
    recording current_user.org_id would attribute the event to the wrong
    organization whenever someone manages an org that isn't their active
    session one. Best-effort, same as the original."""
    try:
        from db.engine import get_sessionmaker
        from db.models import AuditEvent

        async with get_sessionmaker()() as session:
            session.add(AuditEvent(actor_id=user_id, org_id=org_id, action=action, target=target, created_at=int(time.time())))
            await session.commit()
    except Exception as e:
        logger.error("audit_log_write_failed", action=action, error=str(e))


async def _org_read_rate_limit(org_id: int) -> None:
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:read:org:{org_id}", settings.read_path_rate_limit_capacity,
        settings.read_path_rate_limit_refill_per_second, kind="read",
    )


async def _org_write_rate_limit(org_id: int, user_id: int) -> None:
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:write:org:{org_id}:{user_id}", settings.log_step_rate_limit_capacity,
        settings.log_step_rate_limit_refill_per_second, kind="org_write",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /me, POST /auth/switch-project (plan §9.1)
# ─────────────────────────────────────────────────────────────────────────────

class SwitchProjectRequest(BaseModel):
    project_id: int


@router.get("/me")
async def get_me(current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    """The one call the frontend makes on load — every org the user
    belongs to, their role in each, and every project in each org. Backs
    the org switcher, role badge, and every permission-gated control."""
    org_rows = await orgs_db.list_orgs_for_user(current_user.user_id)
    memberships = []
    for org in org_rows:
        projects = await orgs_db.list_projects(org["id"])
        memberships.append({
            "org": {"id": org["id"], "name": org["name"], "plan": org["plan"]},
            "role": org["role"], "joinedAt": org["createdAt"],
            "projects": [{"id": p["id"], "name": p["name"], "environment": p["environment"]} for p in projects],
        })
    # Phase 4 A2's checkpoint reads this — "emailVerified" is the one
    # field on the user object that isn't already in the JWT payload
    # CurrentUser was built from (verifying doesn't mint a new token).
    user_row = await email_verification_db.get_user_for_resend(current_user.user_id)
    email_verified = user_row["emailVerified"] if user_row is not None else False
    return {
        "user": {
            "id": current_user.user_id, "name": current_user.name, "email": current_user.email,
            "emailVerified": email_verified,
        },
        "active": {"orgId": current_user.org_id, "projectId": current_user.project_id,
                   "role": await permissions.get_role(current_user.user_id, current_user.org_id)},
        "memberships": memberships,
    }


@router.post("/auth/switch-project")
async def switch_project(body: SwitchProjectRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    """Mints a FRESH token scoped to a different project the caller
    already holds a membership in — see auth.py's module docstring for
    why this mints a new token rather than mutating the current one."""
    project = await orgs_db.get_project(body.project_id)
    if project is None:
        raise ApiError(404, f"Project {body.project_id} not found", ErrorCode.PROJECT_NOT_FOUND)
    role = await permissions.get_role(current_user.user_id, project["orgId"])
    if role is None:
        raise ApiError(403, "not a member of this project's organization", ErrorCode.INSUFFICIENT_ROLE)

    token = auth.create_token(
        email=current_user.email, name=current_user.name,
        project_id=project["id"], org_id=project["orgId"], user_id=current_user.user_id,
    )
    return {"token": token, "orgId": project["orgId"], "projectId": project["id"], "role": role}


# ─────────────────────────────────────────────────────────────────────────────
#  Organizations (plan §9.2)
# ─────────────────────────────────────────────────────────────────────────────

class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_name: str = Field(default="Default", min_length=1, max_length=200)


class RenameOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TransferOwnershipRequest(BaseModel):
    user_id: int


@router.get("/orgs")
async def list_orgs(current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    return {"orgs": await orgs_db.list_orgs_for_user(current_user.user_id)}


@router.post("/orgs")
async def create_org_endpoint(body: CreateOrgRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:org-create:{current_user.user_id}", settings.log_step_rate_limit_capacity,
        settings.log_step_rate_limit_refill_per_second, kind="org_create",
    )
    result = await orgs_db.create_org(current_user.user_id, body.name, body.project_name, int(time.time()))
    await audit_log_org_action(current_user.user_id, result["orgId"], "org.created", target=body.name)
    return result


@router.get("/orgs/{org_id}")
async def get_org_endpoint(org_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.ORG_READ)
    await _org_read_rate_limit(org_id)
    org = await orgs_db.get_org(org_id)
    if org is None:
        raise ApiError(404, f"Organization {org_id} not found", ErrorCode.ORG_NOT_FOUND)
    return org


@router.patch("/orgs/{org_id}")
async def rename_org_endpoint(org_id: int, body: RenameOrgRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.ORG_UPDATE)
    if not await orgs_db.rename_org(org_id, body.name):
        raise ApiError(404, f"Organization {org_id} not found", ErrorCode.ORG_NOT_FOUND)
    await audit_log_org_action(current_user.user_id, org_id, "org.renamed", target=body.name)
    return {"ok": True}


@router.delete("/orgs/{org_id}")
async def delete_org_endpoint(org_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.ORG_DELETE)
    if await orgs_db.count_active_orgs_for_user(current_user.user_id) <= 1:
        raise ApiError(400, "cannot delete your only organization", ErrorCode.CANNOT_DELETE_ONLY_ORG)
    if not await orgs_db.soft_delete_org(org_id, int(time.time())):
        raise ApiError(404, f"Organization {org_id} not found", ErrorCode.ORG_NOT_FOUND)
    await audit_log_org_action(current_user.user_id, org_id, "org.deleted")
    return {"ok": True}


@router.post("/orgs/{org_id}/transfer-ownership")
async def transfer_ownership_endpoint(
    org_id: int, body: TransferOwnershipRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.ORG_TRANSFER_OWNERSHIP)
    now = int(time.time())
    if not await orgs_db.transfer_ownership(current_user.user_id, body.user_id, org_id, now):
        raise ApiError(404, "target user is not a member of this organization", ErrorCode.MEMBER_NOT_FOUND)

    await membership_cache.invalidate(current_user.user_id, org_id)
    await membership_cache.invalidate(body.user_id, org_id)
    await audit_log_org_action(current_user.user_id, org_id, "org.ownership_transferred", target=str(body.user_id))

    # A silent ownership transfer is what an account takeover looks like —
    # critical, unconditionally, regardless of who performed it (plan §5.5).
    await alerts_db.raise_alert(
        org_id=org_id, alert_type="ownership_transferred", severity="critical",
        title="Organization ownership was transferred",
        summary=f"Ownership transferred from user {current_user.user_id} to user {body.user_id}.",
        subject=f"org:{org_id}",
        evidence={"fromUser": current_user.user_id, "toUser": body.user_id, "actor": current_user.email},
        detector="api",
    )
    return {"ok": True}


@router.get("/orgs/{org_id}/audit-events")
async def list_audit_events(
    org_id: int, limit: int = Query(default=50, le=200),
    current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.AUDIT_READ)
    await _org_read_rate_limit(org_id)
    from db.engine import get_sessionmaker
    from db.models import AuditEvent
    from sqlalchemy import select as _select

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            _select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.created_at.desc()).limit(limit)
        )).scalars().all()
    return {"events": [
        {"id": e.id, "actorId": e.actor_id, "action": e.action, "target": e.target, "createdAt": e.created_at}
        for e in rows
    ]}


# ─────────────────────────────────────────────────────────────────────────────
#  Projects (plan §9.3)
# ─────────────────────────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    environment: str = Field(default="live", pattern="^(test|live)$")


class RenameProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/orgs/{org_id}/projects")
async def list_projects_endpoint(org_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.PROJECT_READ)
    await _org_read_rate_limit(org_id)
    return {"projects": await orgs_db.list_projects(org_id)}


@router.post("/orgs/{org_id}/projects")
async def create_project_endpoint(
    org_id: int, body: CreateProjectRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.PROJECT_CREATE)
    await _org_write_rate_limit(org_id, current_user.user_id)
    project = await orgs_db.create_project(org_id, body.name, body.environment, int(time.time()))
    await audit_log_org_action(current_user.user_id, org_id, "project.created", target=body.name)
    return project


@router.get("/projects/{project_id}")
async def get_project_endpoint(project_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    project = await orgs_db.get_project(project_id)
    if project is None:
        raise ApiError(404, f"Project {project_id} not found", ErrorCode.PROJECT_NOT_FOUND)
    await permissions.require_permission(current_user.user_id, project["orgId"], permissions.Permission.PROJECT_READ)
    return project


@router.patch("/projects/{project_id}")
async def rename_project_endpoint(
    project_id: int, body: RenameProjectRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    project = await orgs_db.get_project(project_id)
    if project is None:
        raise ApiError(404, f"Project {project_id} not found", ErrorCode.PROJECT_NOT_FOUND)
    await permissions.require_permission(current_user.user_id, project["orgId"], permissions.Permission.PROJECT_UPDATE)
    await orgs_db.rename_project(project_id, body.name)
    await audit_log_org_action(current_user.user_id, project["orgId"], "project.renamed", target=body.name)
    return {"ok": True}


@router.delete("/projects/{project_id}")
async def delete_project_endpoint(project_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    project = await orgs_db.get_project(project_id)
    if project is None:
        raise ApiError(404, f"Project {project_id} not found", ErrorCode.PROJECT_NOT_FOUND)
    await permissions.require_permission(current_user.user_id, project["orgId"], permissions.Permission.PROJECT_DELETE)
    if await orgs_db.count_active_projects(project["orgId"]) <= 1:
        raise ApiError(400, "cannot delete the organization's last project", ErrorCode.CANNOT_DELETE_LAST_PROJECT)
    await orgs_db.soft_delete_project(project_id, int(time.time()))
    await audit_log_org_action(current_user.user_id, project["orgId"], "project.deleted", target=str(project_id))
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Members (plan §9.4, §5.5)
# ─────────────────────────────────────────────────────────────────────────────

class ChangeRoleRequest(BaseModel):
    role: str


@router.get("/orgs/{org_id}/members")
async def list_members_endpoint(org_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_READ)
    await _org_read_rate_limit(org_id)
    members = await orgs_db.list_members(org_id)
    return {"members": members, "total": len(members)}


@router.patch("/orgs/{org_id}/members/{user_id}")
async def change_role_endpoint(
    org_id: int, user_id: int, body: ChangeRoleRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    actor_role = await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_ROLE_CHANGE)
    if body.role not in permissions.VALID_ROLES or body.role == "owner":
        raise ApiError(400, f"invalid role: {body.role!r}", ErrorCode.INVALID_ROLE)
    if user_id == current_user.user_id:
        raise ApiError(400, "cannot change your own role", ErrorCode.CANNOT_MODIFY_OWN_ROLE)

    target = await orgs_db.get_membership(user_id, org_id)
    if target is None:
        raise ApiError(404, f"user {user_id} is not a member of organization {org_id}", ErrorCode.MEMBER_NOT_FOUND)

    actor_rank, target_rank, requested_rank = permissions.rank_of(actor_role), permissions.rank_of(target["role"]), permissions.rank_of(body.role)
    if not orgs_db.rank_allows_target_modification(actor_rank, target_rank, requested_rank):
        raise ApiError(403, "cannot modify a member at or above your own rank", ErrorCode.INSUFFICIENT_ROLE)
    if target["role"] == "owner" and await orgs_db.count_owners(org_id) <= 1:
        raise ApiError(400, "cannot demote the organization's last owner", ErrorCode.CANNOT_REMOVE_LAST_OWNER)

    await orgs_db.change_role(user_id, org_id, body.role, int(time.time()))
    await membership_cache.invalidate(user_id, org_id)
    observability.MEMBERSHIP_CHANGES_TOTAL.labels(action="role_changed").inc()
    await audit_log_org_action(current_user.user_id, org_id, "member.role_changed", target=f"user:{user_id}->{body.role}")

    if body.role in ("admin", "owner"):
        await alerts_db.raise_alert(
            org_id=org_id, alert_type="admin_role_granted", severity="warning",
            title=f"User {user_id} was granted {body.role}",
            summary=f"Role changed to {body.role} by {current_user.email}.",
            subject=f"user:{user_id}", evidence={"targetUser": user_id, "newRole": body.role, "actor": current_user.email},
            detector="api",
        )
    return {"ok": True}


@router.delete("/orgs/{org_id}/members/me")
async def leave_org_endpoint(org_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    """Registered BEFORE DELETE /orgs/{org_id}/members/{user_id} below,
    deliberately — both are single-segment paths after .../members/, and
    FastAPI/Starlette matches route DEFINITIONS in registration order,
    not by preferring a literal segment over a path-param one. With the
    {user_id}: int route registered first, a request for .../members/me
    would match ITS pattern first, fail int("me") validation, and 422
    instead of ever reaching this handler — a real bug this exact
    ordering caught via test_memberships.py's real HTTP-level tests
    (test_cannot_remove_the_last_owner / test_member_can_leave_voluntarily),
    not by inspection. Same ordering requirement GET /alerts/summary and
    GET /alerts/stream already have relative to GET /alerts/{alert_id}."""
    membership = await orgs_db.get_membership(current_user.user_id, org_id)
    if membership is None:
        raise ApiError(404, f"not a member of organization {org_id}", ErrorCode.MEMBER_NOT_FOUND)
    if membership["role"] == "owner" and await orgs_db.count_owners(org_id) <= 1:
        raise ApiError(400, "cannot leave — you are the last owner; transfer ownership first", ErrorCode.CANNOT_REMOVE_LAST_OWNER)

    await orgs_db.remove_member(current_user.user_id, org_id)
    await membership_cache.invalidate(current_user.user_id, org_id)
    observability.MEMBERSHIP_CHANGES_TOTAL.labels(action="removed").inc()
    await audit_log_org_action(current_user.user_id, org_id, "member.left")
    return {"ok": True}


@router.delete("/orgs/{org_id}/members/{user_id}")
async def remove_member_endpoint(org_id: int, user_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    actor_role = await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_REMOVE)
    target = await orgs_db.get_membership(user_id, org_id)
    if target is None:
        raise ApiError(404, f"user {user_id} is not a member of organization {org_id}", ErrorCode.MEMBER_NOT_FOUND)
    if permissions.rank_of(target["role"]) >= permissions.rank_of(actor_role):
        raise ApiError(403, "cannot remove a member at or above your own rank", ErrorCode.INSUFFICIENT_ROLE)
    if target["role"] == "owner" and await orgs_db.count_owners(org_id) <= 1:
        raise ApiError(400, "cannot remove the organization's last owner", ErrorCode.CANNOT_REMOVE_LAST_OWNER)

    await orgs_db.remove_member(user_id, org_id)
    await membership_cache.invalidate(user_id, org_id)
    observability.MEMBERSHIP_CHANGES_TOTAL.labels(action="removed").inc()
    await audit_log_org_action(current_user.user_id, org_id, "member.removed", target=f"user:{user_id}")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Invitations (plan §5, §9.5)
# ─────────────────────────────────────────────────────────────────────────────

class CreateInvitationRequest(BaseModel):
    email:  EmailStr
    role:   str


class AcceptInvitationRequest(BaseModel):
    pass


@router.post("/orgs/{org_id}/invitations")
async def create_invitation_endpoint(
    org_id: int, body: CreateInvitationRequest, request: Request,
    current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_INVITE)
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "invitation-create", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    await rate_limit.enforce_rate_limit(
        f"ratelimit:invitation:{org_id}", settings.invitation_rate_limit_capacity,
        settings.invitation_rate_limit_refill_per_second, kind="invitation",
    )

    if body.role not in ("admin", "member", "viewer"):
        raise ApiError(400, f"invalid role: {body.role!r}", ErrorCode.INVALID_ROLE)

    now = int(time.time())
    if await invitations_db.count_pending(org_id, now) >= settings.max_pending_invitations_per_org:
        raise ApiError(400, "too many pending invitations for this organization", ErrorCode.TOO_MANY_PENDING_INVITATIONS)

    existing_membership_role = None
    for m in await orgs_db.list_members(org_id):
        if m["email"].lower() == body.email.lower():
            existing_membership_role = m["role"]
    if existing_membership_role is not None:
        raise ApiError(409, "this email is already a member", ErrorCode.ALREADY_A_MEMBER)

    try:
        result = await invitations_db.create_invitation(
            org_id, body.email, body.role, current_user.user_id, now, settings.invitation_ttl_seconds,
        )
    except ValueError as e:
        raise ApiError(409, str(e), ErrorCode.TOO_MANY_PENDING_INVITATIONS)

    observability.INVITATIONS_TOTAL.labels(action="created").inc()
    await audit_log_org_action(current_user.user_id, org_id, "member.invited", target=body.email)

    # Queued for delivery via notifications/sender.py — see main.py's
    # module-level note on why this doesn't send inline (Phase 3 §7.4/§5.3).
    from notifications.invite import queue_invitation_email
    await queue_invitation_email(
        org_id=org_id, email=body.email, role=body.role, raw_token=result["rawToken"],
        invited_by_name=current_user.name, now=now,
    )

    return {"id": result["id"], "email": body.email, "role": body.role, "expiresAt": result["expiresAt"], "status": "pending"}


@router.get("/orgs/{org_id}/invitations")
async def list_invitations_endpoint(
    org_id: int, status: Optional[str] = Query(default=None),
    current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_INVITE)
    await _org_read_rate_limit(org_id)
    return {"invitations": await invitations_db.list_invitations(org_id, status)}


@router.delete("/orgs/{org_id}/invitations/{invitation_id}")
async def revoke_invitation_endpoint(
    org_id: int, invitation_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_INVITE)
    if not await invitations_db.revoke_invitation(invitation_id, org_id, current_user.user_id, int(time.time())):
        raise ApiError(404, f"pending invitation {invitation_id} not found", ErrorCode.INVITATION_NOT_FOUND)
    observability.INVITATIONS_TOTAL.labels(action="revoked").inc()
    await audit_log_org_action(current_user.user_id, org_id, "member.invite_revoked", target=str(invitation_id))
    return {"ok": True}


@router.post("/orgs/{org_id}/invitations/{invitation_id}/resend")
async def resend_invitation_endpoint(
    org_id: int, invitation_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    """Issues a NEW token (invalidating the old hash) and re-queues
    delivery — implemented as revoke-then-recreate rather than a dedicated
    code path, so it inherits the exact same validation, rate limiting,
    and pending-count ceiling as creating a fresh invitation."""
    await permissions.require_permission(current_user.user_id, org_id, permissions.Permission.MEMBER_INVITE)
    settings = get_settings()
    await rate_limit.enforce_rate_limit(
        f"ratelimit:invitation:{org_id}", settings.invitation_rate_limit_capacity,
        settings.invitation_rate_limit_refill_per_second, kind="invitation",
    )
    now = int(time.time())
    pending = await invitations_db.list_invitations(org_id, status="pending", now=now)
    match = next((i for i in pending if i["id"] == invitation_id), None)
    if match is None:
        raise ApiError(404, f"pending invitation {invitation_id} not found", ErrorCode.INVITATION_NOT_FOUND)

    await invitations_db.revoke_invitation(invitation_id, org_id, current_user.user_id, now)
    result = await invitations_db.create_invitation(
        org_id, match["email"], match["role"], current_user.user_id, now, settings.invitation_ttl_seconds,
    )
    from notifications.invite import queue_invitation_email
    await queue_invitation_email(
        org_id=org_id, email=match["email"], role=match["role"], raw_token=result["rawToken"],
        invited_by_name=current_user.name, now=now,
    )
    return {"id": result["id"], "email": match["email"], "role": match["role"], "expiresAt": result["expiresAt"]}


@router.get("/invitations/{token}")
async def preview_invitation(token: str, request: Request):
    """Unauthenticated by design — the recipient has no account yet
    (Phase 3 §5.1/§9.5). Any invalid/expired/revoked/used token returns
    the SAME 404 so a scanner learns nothing from the difference."""
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "invitation-preview", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )
    preview = await invitations_db.get_invitation_preview(token, int(time.time()))
    if preview is None:
        raise ApiError(404, "invitation not found, expired, or already used", ErrorCode.INVITATION_NOT_FOUND)
    return preview


@router.post("/invitations/{token}/accept")
async def accept_invitation_endpoint(token: str, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    """The already-registered-user path (Phase 3 §5.4) — POST /auth/signup's
    invite_token field is the new-user path instead."""
    result = await invitations_db.accept_for_existing_user(token, current_user.user_id, current_user.email, int(time.time()))
    observability.INVITATIONS_TOTAL.labels(action="accepted").inc()
    await audit_log_org_action(current_user.user_id, result["orgId"], "member.joined")

    new_token = auth.create_token(
        email=current_user.email, name=current_user.name,
        project_id=result["projectId"], org_id=result["orgId"], user_id=current_user.user_id,
    )
    return {"membership": {"orgId": result["orgId"], "role": result["role"]}, "token": new_token}


# ─────────────────────────────────────────────────────────────────────────────
#  Alerts (plan §6, §7, §9.6)
# ─────────────────────────────────────────────────────────────────────────────

class ResolveAlertRequest(BaseModel):
    resolution_note: str = Field(default="", max_length=2000)


class AcknowledgeAlertRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


async def _authorize_alert_read(principal: auth.Principal) -> None:
    """GET /alerts and friends are reachable by EITHER credential type
    (plan §10.1 — "pipe TrustChain alerts into your own on-call tooling"
    needs a project API key to work, not just a human session), unlike
    every other Phase 3 endpoint in this file. The two credential types
    use their own, different authorization mechanism rather than one
    unified check: an API key's authority is its granted scopes
    (alerts:read) — full-stop, no separate role concept exists for a
    machine credential. A human/JWT principal still goes through the real
    role-based permission matrix (viewer+)."""
    if principal.is_api_key:
        auth.require_scope(principal, "alerts:read")
    else:
        await permissions.require_permission(principal.user_id, principal.org_id, permissions.Permission.ALERT_READ)


@router.get("/alerts")
async def list_alerts_endpoint(
    status: Optional[str] = Query(default=None), severity: Optional[str] = Query(default=None),
    alert_type: Optional[str] = Query(default=None), project_id: Optional[int] = Query(default=None),
    before_id: Optional[int] = Query(default=None), limit: int = Query(default=50, le=200),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    await _authorize_alert_read(principal)
    await _org_read_rate_limit(principal.org_id)
    rows, total_open = await alerts_db.list_alerts(
        principal.org_id, status=status, severity=severity, alert_type=alert_type,
        project_id=project_id, limit=limit, before_id=before_id,
    )
    next_cursor = rows[-1]["id"] if len(rows) == limit else None
    return {"alerts": rows, "nextCursor": next_cursor, "totalOpen": total_open}


@router.get("/alerts/summary")
async def alert_summary_endpoint(principal: auth.Principal = Depends(auth.get_current_principal)):
    await _authorize_alert_read(principal)
    return await alerts_db.alert_summary(principal.org_id)


@router.get("/alerts/stream")
async def alerts_stream_endpoint(request: Request, principal: auth.Principal = Depends(auth.get_current_principal)):
    """Live push for the alert badge/inbox — a bounded, from-now-only
    Redis stream (alert_events.py), NOT a replacement for GET /alerts
    (Postgres remains the system of record for full history; a client
    should call that on load, then subscribe here for deltas). Registered
    ABOVE /alerts/{alert_id} so 'stream' is never swallowed as an
    alert_id path param — see GET /alerts/summary's identical ordering
    requirement just above."""
    await _authorize_alert_read(principal)
    settings = get_settings()
    await rate_limit.enforce_ip_rate_limit(
        request, "alerts-stream", settings.ip_rate_limit_capacity, settings.ip_rate_limit_refill_per_second,
    )

    async def event_generator():
        try:
            async for event in alert_events.read_alert_events(principal.org_id):
                if event is None:
                    yield ": keep-alive\n\n"  # SSE comment line — no-op, just keeps the connection/proxies alive
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            logger.info("alerts_sse_client_disconnected", org_id=principal.org_id)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/alerts/{alert_id}")
async def get_alert_endpoint(alert_id: int, principal: auth.Principal = Depends(auth.get_current_principal)):
    await _authorize_alert_read(principal)
    alert = await alerts_db.get_alert(alert_id, principal.org_id)
    if alert is None:
        raise ApiError(404, f"alert {alert_id} not found", ErrorCode.ALERT_NOT_FOUND)
    return alert


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert_endpoint(
    alert_id: int, body: AcknowledgeAlertRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, current_user.org_id, permissions.Permission.ALERT_ACKNOWLEDGE)
    if not await alerts_db.acknowledge_alert(alert_id, current_user.org_id, current_user.user_id, int(time.time())):
        raise ApiError(404, f"open alert {alert_id} not found", ErrorCode.ALERT_NOT_FOUND)
    await audit_log_org_action(current_user.user_id, current_user.org_id, "alert.acknowledged", target=str(alert_id))
    return {"ok": True}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert_endpoint(
    alert_id: int, body: ResolveAlertRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    await permissions.require_permission(current_user.user_id, current_user.org_id, permissions.Permission.ALERT_RESOLVE)
    if not await alerts_db.resolve_alert(alert_id, current_user.org_id, current_user.user_id, int(time.time()), body.resolution_note):
        raise ApiError(409, f"alert {alert_id} not found or already resolved", ErrorCode.ALERT_ALREADY_RESOLVED)
    await audit_log_org_action(current_user.user_id, current_user.org_id, "alert.resolved", target=str(alert_id))
    return {"ok": True}


@router.post("/alerts/{alert_id}/reopen")
async def reopen_alert_endpoint(alert_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await permissions.require_permission(current_user.user_id, current_user.org_id, permissions.Permission.ALERT_RESOLVE)
    if not await alerts_db.reopen_alert(alert_id, current_user.org_id, int(time.time())):
        raise ApiError(409, f"alert {alert_id} not found or not resolved", ErrorCode.ALERT_NOT_RESOLVABLE)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Notification preferences (plan §9.8)
# ─────────────────────────────────────────────────────────────────────────────

class SetNotificationPreferencesRequest(BaseModel):
    org_id: int
    email_critical: bool = True
    email_warning: bool = True
    email_info: bool = False
    email_digest_only: bool = False


@router.get("/me/notification-preferences")
async def get_notification_preferences_endpoint(
    org_id: int = Query(...), current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    return await alerts_db.get_notification_preferences(current_user.user_id, org_id)


@router.put("/me/notification-preferences")
async def set_notification_preferences_endpoint(
    body: SetNotificationPreferencesRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user),
):
    # Own preferences only (permissions.Permission.NOTIFICATION_PREFS_MANAGE
    # is "viewer" — any member — deliberately unscoped by role beyond that,
    # since a user managing their OWN notification settings needs no
    # elevated authority; just needs to still be a member of the org).
    role = await permissions.get_role(current_user.user_id, body.org_id)
    if role is None:
        raise ApiError(403, "not a member of this organization", ErrorCode.INSUFFICIENT_ROLE)
    await alerts_db.set_notification_preferences(
        current_user.user_id, body.org_id, body.email_critical, body.email_warning,
        body.email_info, body.email_digest_only, int(time.time()),
    )
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Integrity status & on-demand verification (plan §9.7)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/integrity/status")
async def integrity_status(principal: auth.Principal = Depends(auth.get_current_principal)):
    """'Is everything OK right now' for the caller's active project —
    reads the watchdog's own persisted cursor/coverage state
    (watchdog_cursor, batch_verifications) rather than running anything
    live; see POST /integrity/verify-run/{run_id} for an immediate,
    synchronous, single-run check instead."""
    auth.require_scope(principal, "runs:read")
    from sqlalchemy import select as _select, func as _func, text
    from db.engine import get_sessionmaker
    from db.models import AnchorBatch, BatchVerification, Run, Step

    async with get_sessionmaker()() as session:
        steps_verified = (await session.execute(
            _select(_func.count()).select_from(Step).join(Run, Run.run_id == Step.run_id)
            .where(Run.project_id == principal.project_id)
        )).scalar_one()
        batch_ids = (await session.execute(
            _select(AnchorBatch.id).join(Step, Step.anchor_batch_id == AnchorBatch.id)
            .join(Run, Run.run_id == Step.run_id).where(Run.project_id == principal.project_id).distinct()
        )).scalars().all()
        batches_verified = len(batch_ids)
        batches_root_confirmed = 0
        if batch_ids:
            batches_root_confirmed = (await session.execute(
                _select(_func.count()).select_from(BatchVerification)
                .where(BatchVerification.batch_id.in_(batch_ids), BatchVerification.last_result == "ok")
            )).scalar_one()

        cursors = []
        for detector in ("step_rows_rolling", "merkle_roots_rolling"):
            result = await session.execute(
                text("SELECT last_id, wrapped_at, last_run_at, last_duration_ms FROM watchdog_cursor WHERE detector = :d"),
                {"d": detector},
            )
            row = result.first()
            cursors.append({
                "name": detector, "lastRunAt": row.last_run_at if row else 0,
                "lastDurationMs": row.last_duration_ms if row else 0,
                "cursorPosition": row.last_id if row else 0, "wrappedAt": row.wrapped_at if row else None,
            })

    project = await orgs_db.get_project(principal.project_id)
    open_critical = open_warning = 0
    last_sweep_at = max((c["lastRunAt"] for c in cursors), default=0)
    if project is not None:
        summary = await alerts_db.alert_summary(project["orgId"])
        open_critical, open_warning = summary["open"]["critical"], summary["open"]["warning"]

    return {
        "projectId": principal.project_id, "lastSweepAt": last_sweep_at or None,
        "stepsVerified": steps_verified, "batchesVerified": batches_verified,
        "batchesRootConfirmed": batches_root_confirmed,
        "coveragePercent": round(100.0 * batches_root_confirmed / batches_verified, 1) if batches_verified else 100.0,
        "openCritical": open_critical, "openWarning": open_warning, "detectors": cursors,
    }


@router.post("/integrity/verify-run/{run_id}")
async def verify_run(run_id: str, principal: auth.Principal = Depends(auth.get_current_principal)):
    """Runs detectors 3 and 4 against every step in ONE run, synchronously
    — 'is this specific run still intact right now', answered immediately
    rather than waiting for the watchdog's rolling sweep to reach it.
    Deliberately expensive (real Merkle rebuilds, real RPC calls if the
    run's batches haven't been on-chain-verified yet) — rate limited
    harder than an ordinary read."""
    auth.require_scope(principal, "runs:read")
    await rate_limit.enforce_rate_limit(
        f"ratelimit:verify-run:{principal.project_id}", 6.0, 6.0 / 60, kind="verify_run",
    )
    from sqlalchemy import select as _select
    from db.engine import get_sessionmaker
    from db.models import AnchorBatch, Run, Step
    from integrity_watchdog.detectors import merkle_roots, step_rows

    async with get_sessionmaker()() as session:
        run = await session.get(Run, run_id)
        if run is None or run.project_id != principal.project_id:
            raise ApiError(404, f"run '{run_id}' not found", ErrorCode.RUN_NOT_FOUND)

        steps = (await session.execute(_select(Step).where(Step.run_id == run_id).order_by(Step.step_index))).scalars().all()
        if not steps:
            return {"runId": run_id, "steps": [], "allVerified": True}

        row_mismatches = {m["step"].id for m in await step_rows.check_steps(steps)}

        batch_ids = {s.anchor_batch_id for s in steps if s.anchor_batch_id is not None}
        root_bad_batches: set[int] = set()
        for batch_id in batch_ids:
            batch = await session.get(AnchorBatch, batch_id)
            if batch is None:
                continue
            batch_steps = (await session.execute(_select(Step).where(Step.id.in_(batch.leaf_order)))).scalars().all()
            steps_by_id = {s.id: s for s in batch_steps}
            rebuilt_root, missing = merkle_roots.rebuild_root(steps_by_id, list(batch.leaf_order))
            if missing or rebuilt_root.lower() != batch.merkle_root.lower():
                root_bad_batches.add(batch_id)
                continue
            if batch.onchain_anchor_id is not None:
                try:
                    matches, _ = await merkle_roots.check_onchain_root(batch)
                    if not matches:
                        root_bad_batches.add(batch_id)
                except Exception as e:
                    logger.warning("verify_run_onchain_check_failed", run_id=run_id, batch_id=batch_id, error=str(e))

    results = []
    all_ok = True
    for step in steps:
        ok = step.id not in row_mismatches and (step.anchor_batch_id is None or step.anchor_batch_id not in root_bad_batches)
        anchored = step.anchor_batch_id is not None
        results.append({
            "stepId": step.id, "stepIndex": step.step_index, "agentId": step.agent_id,
            "verified": ok, "anchored": anchored,
            "reason": None if ok else ("row_hash_mismatch" if step.id in row_mismatches else "batch_root_mismatch"),
        })
        all_ok = all_ok and ok

    return {"runId": run_id, "steps": results, "allVerified": all_ok}


@router.post("/integrity/verify-content")
async def verify_content(body: VerifyContentRequest, principal: auth.Principal = Depends(auth.get_current_principal)):
    """Turns an opaque hash sitting in an alert's evidence into an actual
    yes/no answer about content — WITHOUT TrustChain ever storing that
    content itself (see ADR-0020's 'what changed' discussion for exactly
    why raw agent input/output text isn't persisted here: input_hash/
    output_hash are one-way hashes, so nobody — including TrustChain
    itself — can work backwards from them to recover text nobody kept a
    copy of).

    What this DOES let an owner do: they supply a CANDIDATE piece of text
    they already have from their OWN systems (their own application
    logs, an observability tool, wherever their agent framework actually
    logs transcripts) — this hashes it the identical way
    agents/base.py::log_step does and reports whether it matches the
    step's CURRENT stored hash, and — if steps_history recorded this
    step being modified — whether it instead matches what the hash WAS
    before that change. That second case is the actual answer to "what
    did this used to say": not TrustChain telling you, but TrustChain
    confirming or denying a candidate YOU already suspected.

    This is verification of one candidate at a time, not a search over
    unknown content — rate limited the same as verify-run for the same
    reason (real Merkle rebuilds there, a real hash computation here;
    neither should be hammerable)."""
    auth.require_scope(principal, "runs:read")
    await rate_limit.enforce_rate_limit(
        f"ratelimit:verify-content:{principal.project_id}", 6.0, 6.0 / 60, kind="verify_content",
    )

    from web3 import Web3
    from sqlalchemy import select as _select, desc as _desc
    from db.engine import get_sessionmaker
    from db.models import Run, Step, StepHistory

    computed_hash = "0x" + Web3.solidity_keccak(["string"], [body.candidateText]).hex()

    async with get_sessionmaker()() as session:
        step = await session.get(Step, body.stepId)
        if step is None:
            raise ApiError(404, f"step {body.stepId} not found", ErrorCode.STEP_NOT_FOUND)
        run = await session.get(Run, step.run_id)
        if run is None or run.project_id != principal.project_id:
            # Same 404-not-403 shape as verify_run above — a step ID from
            # another tenant should look identical to a nonexistent one.
            raise ApiError(404, f"step {body.stepId} not found", ErrorCode.STEP_NOT_FOUND)

        current_hash = step.input_hash if body.field == "input" else step.output_hash
        matches_current = computed_hash.lower() == current_hash.lower()

        history = (await session.execute(
            _select(StepHistory).where(StepHistory.step_id == body.stepId).order_by(_desc(StepHistory.changed_at)).limit(1)
        )).scalar_one_or_none()

        matches_original = None  # None = "no history to compare against", distinct from False
        if history is not None:
            old_hash = history.old_input_hash if body.field == "input" else history.old_output_hash
            if old_hash is not None:
                matches_original = computed_hash.lower() == old_hash.lower()

    return {
        "stepId": body.stepId, "field": body.field, "computedHash": computed_hash,
        "matchesCurrent": matches_current, "matchesOriginal": matches_original,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Mount `router` twice — see this file's earlier "API versioning" note
#  for why both the unprefixed legacy shape and the /v1 canonical one
#  stay live indefinitely, pointing at the identical handlers.
# ─────────────────────────────────────────────────────────────────────────────

app.include_router(router)
app.include_router(router, prefix="/v1")
# v1_only_router (defined above `router`) holds the 3 routes whose
# Appendix-A-documented /v1 name doesn't literally match their legacy
# unprefixed name — mounted under /v1 ONLY, since there is no
# corresponding unprefixed legacy path to also register it under.
app.include_router(v1_only_router, prefix="/v1")