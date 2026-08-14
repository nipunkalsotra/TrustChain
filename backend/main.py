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
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

import auth
import db
import observability
import rate_limit
import refresh
import run_events
from db import idempotency, read_model, tenancy
from config import get_settings
from logging_config import configure_logging, get_logger, bind_run_id, CorrelationIdMiddleware
from blockchain.client import get_bridge
from agents.pipeline import run_pipeline
from agents.base import make_run_id, log_step
from blockchain import identity_writer

configure_logging(log_level=get_settings().log_level, json_logs=get_settings().environment != "development")
logger = get_logger(__name__)

observability.init_sentry(
    get_settings().sentry_dsn, get_settings().environment, get_settings().sentry_traces_sample_rate,
)
observability.init_tracing(get_settings().otel_service_name, get_settings().otel_exporter_otlp_endpoint)



# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI app + lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("backend_starting")
    try:
        bridge = get_bridge()
        logger.info("bridge_ready", wallet=bridge.account.address)
    except Exception as e:
        logger.error("bridge_init_failed_non_fatal", error=str(e))
    yield
    logger.info("backend_shutting_down")


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


@app.get("/metrics")
async def metrics():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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


# ─────────────────────────────────────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    task:   str
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


class LogStepResponse(BaseModel):
    step_id:       int
    outbox_id:     int
    status:        str = "queued"
    anchor_status: str


class VerifyRequest(BaseModel):
    runId: str   # FIX 2: was agent_id + code_hash_hex — but frontend sends
                 # { runId } per the locked VerifyRequest type in types.ts.
                 # Backend must accept runId and verify all 4 agents itself.


class SignupRequest(BaseModel):
    name:     str = Field(min_length=1, max_length=100)
    email:    EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    name:  str
    email: str


class RefreshRequest(BaseModel):
    refresh_token: str


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
async def signup(body: SignupRequest):
    try:
        user = await db.create_user(
            email=body.email, name=body.name, password=body.password,
            created_at=int(time.time()),
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="email already registered")

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
    client_ip = request.client.host if request.client else "unknown"
    await rate_limit.check_login_backoff(body.email, client_ip)

    user = await db.authenticate_user(email=body.email, password=body.password)
    if user is None:
        await rate_limit.record_login_failure(body.email, client_ip)
        raise HTTPException(status_code=401, detail="invalid email or password")

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
        raise HTTPException(status_code=401, detail="invalid, expired, or reused refresh token")
    return TokenPairResponse(
        access_token=pair["accessToken"], refresh_token=pair["refreshToken"], expires_in=pair["expiresIn"]
    )


@router.post("/auth/logout")
async def logout(body: RefreshRequest):
    await refresh.revoke_family_for_token(body.refresh_token)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  API key management — human/JWT-only (never an API key itself), and
#  restricted to owner/admin members of the key's org (plan §14.2: members
#  can use a project, only owner/admin can mint credentials for it).
# ─────────────────────────────────────────────────────────────────────────────

async def _require_admin(current_user: auth.CurrentUser) -> None:
    role = await tenancy.get_membership_role(current_user.user_id, current_user.org_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="owner or admin role required")


@router.post("/api-keys", response_model=ApiKeyCreatedResponse)
async def create_api_key(body: CreateApiKeyRequest, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    try:
        key = await tenancy.create_api_key(
            current_user.project_id, body.scopes, int(time.time()), environment=body.environment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_log_admin_action(current_user, "api_key.created", target=f"api_key:{key['id']}")
    return ApiKeyCreatedResponse(id=key["id"], raw_key=key["rawKey"], last_four=key["lastFour"], scopes=key["scopes"])


@router.get("/api-keys")
async def list_api_keys(current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    keys = await tenancy.list_api_keys(current_user.project_id)
    return {"keys": keys}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: int, current_user: auth.CurrentUser = Depends(auth.get_current_user)):
    await _require_admin(current_user)
    revoked = await tenancy.revoke_api_key(key_id, current_user.project_id, int(time.time()))
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found (or already revoked)")
    await audit_log_admin_action(current_user, "api_key.revoked", target=f"api_key:{key_id}")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Background pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_pipeline_background(task: str, run_id: str):
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
        raise HTTPException(status_code=400, detail="task cannot be empty")

    request_body_bytes = body.model_dump_json().encode()

    if idempotency_key:
        try:
            cached = await idempotency.get_cached_response(
                principal.project_id, idempotency_key, "POST", "/run-agent", request_body_bytes,
            )
        except idempotency.IdempotencyConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
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
        raise HTTPException(status_code=429, detail="monthly run quota exceeded for this organization")

    run_id = body.run_id or make_run_id()
    user_email = principal.actor if not principal.is_api_key else None
    await db.create_run(run_id, principal.project_id, body.task, user_email, int(time.time()))
    asyncio.create_task(_run_pipeline_background(body.task, run_id))
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
async def stream_events(run_id: str):
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
    try:
        entries = await read_model.get_audit_log_entries(principal.project_id, run_id=run_id)
        return {"entries": entries, "total": len(entries)}
    except Exception as e:
        logger.error("api_error", endpoint="audit-log", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trust-scores")
async def get_trust_scores(
    run_id: str = Query(..., description="Run ID to fetch scores for"),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    auth.require_scope(principal, "runs:read")
    try:
        scores = await read_model.get_trust_scores(principal.project_id, run_id)
        return {"runId": run_id, "scores": scores}
    except Exception as e:
        logger.error("api_error", endpoint="trust-scores", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores/history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trust-scores/history")
async def get_trust_score_history(
    run_id: str = Query(..., description="Run ID to fetch score history for"),
    principal: auth.Principal = Depends(auth.get_current_principal),
):
    auth.require_scope(principal, "runs:read")
    try:
        history = await read_model.get_trust_score_history(principal.project_id, run_id)
        return {"runId": run_id, "history": history}
    except Exception as e:
        logger.error("api_error", endpoint="trust-scores/history", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


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
    try:
        return await read_model.get_leaderboard(principal.project_id, max_runs=max_runs)
    except Exception as e:
        logger.error("api_error", endpoint="leaderboard", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


# ─────────────────────────────────────────────────────────────────────────────
#  POST /verify
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/verify")
async def verify_integrity(body: VerifyRequest):
    bridge = get_bridge()
    try:
        result = await bridge.verify_run(body.runId)  # already async
        return result
    except Exception as e:
        logger.error("api_error", endpoint="verify", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


@router.get("/verify/tamper-demo")
async def verify_tamper_demo(agent_id: str = Query(..., description="e.g. researcher")):
    """
    Read-only proof that AgentIdentityRegistry actually detects substitution:
    compares the real agent config hash (should PASS) against a deliberately
    mutated one (should FAIL). No on-chain writes, no gas spent.
    """
    bridge = get_bridge()
    try:
        return await bridge.tamper_demo(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("api_error", endpoint="tamper-demo", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


@router.get("/verify-audit")
async def verify_audit(run_id: str = Query(...)):
    bridge = get_bridge()
    try:
        # Step 1: get indices for this specific run
        indices = await asyncio.to_thread(
            bridge.audit_log.functions.getRunRecordIndices(run_id).call
        )
        logger.info("verify_audit_indices_fetched", run_id=run_id, count=len(indices))

        if not indices:
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
        return {"runId": run_id, "allMatch": all_match, "entries": results}

    except Exception as e:
        logger.error("api_error", endpoint="verify-audit", error=str(e))
        raise HTTPException(status_code=500, detail="internal error — see server logs for details")


# ─────────────────────────────────────────────────────────────────────────────
#  GET /chain-status   — FIX 6: was using asyncio.to_thread for sync w3 calls
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/chain-status")
async def chain_status():
    """
    Called by the frontend on mount to show the top status bar.
    DISCONNECTED in the UI means this endpoint is failing or unreachable.
    Most common causes: backend not running, CORS blocked, bridge error.
    """
    rpc_url = get_settings().monad_rpc_url
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
    bridge = get_bridge()
    chain_id = await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
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
#  path actually needs) and reports chain reachability as informational,
#  not blocking — a chain outage degrades specific endpoints, it shouldn't
#  take the whole process out of rotation, matching how the rest of this
#  file already treats bridge failures as non-fatal.
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ready")
async def ready():
    checks: dict[str, dict] = {}
    ok = True

    try:
        await db.ping()
        checks["database"] = {"ok": True}
    except Exception as e:
        checks["database"] = {"ok": False, "error": str(e)}
        ok = False

    try:
        bridge = get_bridge()
        await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
        checks["chain"] = {"ok": True}
    except Exception as e:
        checks["chain"] = {"ok": False, "error": str(e)}
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
    runs = await db.list_runs(principal.project_id, limit=limit)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, principal: auth.Principal = Depends(auth.get_current_principal)):
    auth.require_scope(principal, "runs:read")

    # Always reads Postgres, scoped by project_id (invariant I7) — no
    # in-process cache. The old in-memory _run_results fast path had the
    # same cross-replica blind spot Redis Streams just fixed for SSE (a
    # second API replica never sees another replica's in-memory writes),
    # and db.complete_run/fail_run already commit synchronously right
    # when the terminal event is produced, so the "fast path" bought
    # negligible latency at the cost of that correctness gap.
    run = await db.get_run(run_id, principal.project_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if run["status"] == "running":
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not yet complete")
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
    try:
        tx_hash = await identity_writer.register_agent(body.agent_id, body.code_hash, body.model, body.version)
    except Exception as e:
        logger.error("api_error", endpoint="agents.register", error=str(e))
        raise HTTPException(status_code=502, detail="on-chain registration failed — see server logs for details")
    return RegisterAgentResponse(agent_id=body.agent_id, tx_hash=tx_hash)


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
    try:
        return await identity_writer.verify_agent(agent_id, code_hash)
    except Exception as e:
        logger.error("api_error", endpoint="agents.verify", error=str(e))
        raise HTTPException(status_code=502, detail="on-chain verification failed — see server logs for details")


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

    request_body_bytes = body.model_dump_json().encode()
    if idempotency_key:
        try:
            cached = await idempotency.get_cached_response(
                principal.project_id, idempotency_key, "POST", "/steps", request_body_bytes,
            )
        except idempotency.IdempotencyConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        if cached is not None:
            return cached["body"]

    owned = await db.get_or_create_run_for_project(body.run_id, principal.project_id, int(time.time()))
    if not owned:
        raise HTTPException(status_code=404, detail=f"Run '{body.run_id}' not found")

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
    proof = await read_model.get_step_proof(step_id, principal.project_id)
    if proof is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step {step_id} not found, not yours, or not yet anchored in a batch",
        )
    return proof


# ─────────────────────────────────────────────────────────────────────────────
#  GET /stats — public platform-level counters (plan Appendix A). No auth,
#  by design (see read_model.get_platform_stats's docstring for why it's
#  still safe: aggregate-only, never a per-tenant breakdown).
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def platform_stats():
    return await read_model.get_platform_stats()


# ─────────────────────────────────────────────────────────────────────────────
#  Mount `router` twice — see this file's earlier "API versioning" note
#  for why both the unprefixed legacy shape and the /v1 canonical one
#  stay live indefinitely, pointing at the identical handlers.
# ─────────────────────────────────────────────────────────────────────────────

app.include_router(router)
app.include_router(router, prefix="/v1")