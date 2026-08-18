"""
observability.py — Prometheus metrics, OpenTelemetry tracing, Sentry (P2.6).

Every piece here is additive and fails toward "off", never toward
breaking the process it's instrumenting:
  - Metrics are always created (cheap, in-process) but SENTRY_DSN /
    OTEL_EXPORTER_OTLP_ENDPOINT being unset just means nothing is shipped
    anywhere — no network calls, no behavior change, safe default for
    local dev and anyone who hasn't configured a backend yet.
  - Sentry: sentry_sdk installs its own exception hooks and swallows its
    own transport errors internally (a real network failure talking to
    Sentry never surfaces as an exception in caller code) — see
    init_sentry()'s docstring for exactly what's verified locally vs not.
  - OpenTelemetry: spans are created either way (a no-op span costs
    nothing meaningful); only the exporter is conditional.

Cardinality note: no metric label here carries a tenant/project/user
identifier. A counter or gauge with one label value per tenant grows
unbounded as tenants are added — exactly the kind of Prometheus
cardinality blowup that takes down the metrics pipeline itself.
Per-tenant drill-down stays in structured logs (logging_config.py's
request_id/run_id correlation), which don't have that constraint.
"""

from prometheus_client import Counter, Gauge, Histogram

from logging_config import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Metrics — one shared module so a name can't drift between what a
#  process emits and what a Grafana dashboard/Prometheus alert queries for.
# ─────────────────────────────────────────────────────────────────────────────

# ── HTTP (API process) ──────────────────────────────────────────────────
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "path"]
)

# ── Pipeline runs (API process) ─────────────────────────────────────────
PIPELINE_RUNS_TOTAL = Counter(
    "pipeline_runs_total", "Total agent pipeline runs", ["status"]  # started|completed|failed
)
PIPELINE_RUN_DURATION_SECONDS = Histogram(
    "pipeline_run_duration_seconds",
    "Agent pipeline run duration in seconds",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
)
RATE_LIMIT_REJECTIONS_TOTAL = Counter(
    "rate_limit_rejections_total", "Requests rejected by rate limiting",
    ["kind"],  # run_agent|login|register_agent|log_step|ip|read
)
SIGNUP_PWNED_PASSWORD_REJECTIONS_TOTAL = Counter(
    "signup_pwned_password_rejections_total",
    "Signups rejected because the chosen password appears in Have I Been Pwned's "
    "breach corpus (auth_pwned.py) — distinct from rate-limit rejections above, this "
    "is a content-based rejection, not a request-volume one.",
)
ANCHOR_PAYLOAD_PII_DETECTED_TOTAL = Counter(
    "anchor_payload_pii_detected_total",
    "Times agents/base.py::log_step found email-shaped content (pii_patterns.py) in "
    "an input_text/output_text about to be hashed for anchoring — detection only, "
    "never redaction or rejection (see pii_patterns.py's module docstring for why "
    "mutating this content would break independent proof verification). A nonzero "
    "rate here means real anchor payloads worth reviewing, not an automatic incident "
    "— someone's task legitimately mentioning an email address isn't a privacy leak "
    "on its own, only a call worth a human looking at what's actually being anchored.",
    ["kind", "field"],  # kind: email (only pattern implemented so far) | field: input|output
)
TOKEN_BUDGET_REJECTIONS_TOTAL = Counter(
    "token_budget_rejections_total",
    "POST /run-agent requests rejected because their org's cumulative real LLM "
    "token spend (organizations.tokens_spent) has reached its configured "
    "organizations.token_budget ceiling (plan O10) — the run never starts, so "
    "unlike anchor_gas_ceiling_breached_total there is no partial-spend recovery "
    "case here.",
    ["org_id"],
)
LLM_TOKENS_USED_TOTAL = Counter(
    "llm_tokens_used_total",
    "Real cumulative LLM token usage (agents/base.py::track_token_usage's real "
    "usage_metadata total across a run's 5 LLM calls — researcher/validator x2/scorer/"
    "reporter), recorded once per completed run, same number just written to "
    "organizations.tokens_spent (db/tenancy.py::record_token_spend). Distinct from "
    "token_budget_rejections_total (a rejection EVENT for a run that never started) — "
    "this is the actual spend for runs that DID start, the number a cost dashboard or "
    "rate/percentile query over real usage needs that a running Postgres total isn't "
    "suited to answer directly.",
    ["org_id"],
)

# ── Anchor worker (anchor_worker process) ───────────────────────────────
ANCHOR_BATCHES_SUBMITTED_TOTAL = Counter(
    "anchor_batches_submitted_total", "AnchorBatch transactions successfully confirmed"
)
ANCHOR_BATCHES_FAILED_TOTAL = Counter(
    "anchor_batches_failed_total", "AnchorBatch submissions that failed", ["reason"]
)
ANCHOR_BATCHES_REPLACED_TOTAL = Counter(
    "anchor_batches_replaced_total",
    "Replace-by-fee resubmissions of a batch's tx at the same nonce with a bumped fee, "
    "because the previous attempt didn't confirm within confirm_timeout",
)
ANCHOR_BATCH_SIZE_STEPS = Histogram(
    "anchor_batch_size_steps",
    "Number of steps anchored per batch",
    buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256),
)
ANCHOR_OUTBOX_PENDING = Gauge(
    "anchor_outbox_pending", "Steps in anchor_outbox awaiting a batch (sampled each work loop)"
)
ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL = Counter(
    "anchor_outbox_dead_lettered_total",
    "Steps permanently given up on (exhausted anchor_max_attempts) — these will NEVER make it "
    "on-chain without manual intervention (see docs/runbooks.md's dead-lettered-steps entry). "
    "Two distinct code paths increment this: reaper.py (a claim that outlived "
    "anchor_claim_timeout_seconds too many times — a repeatedly-crashing worker) and "
    "main.py::handle_submit_failure (a batch that failed submission — revert or timeout — too "
    "many times). Before this metric existed, both paths only logged (dead_lettered=N /  "
    "last_error), invisible to Prometheus/alerting — a real gap for a product whose entire "
    "point is a complete audit trail: silently losing a step here breaks that guarantee for "
    "exactly the step it happens to.",
    ["source"],  # reaper|submit_failure
)
ANCHOR_REAPER_RESET_TOTAL = Counter(
    "anchor_reaper_reset_total",
    "Outbox rows the reaper reset back to 'pending' after their claim went stale "
    "(anchor_claim_timeout_seconds elapsed with no confirmation) — recovered from a worker "
    "crash/restart, not lost. A high rate here across short windows is a crash-loop signal "
    "worth its own alert even though no data was actually lost (distinct from "
    "anchor_outbox_dead_lettered_total, which IS data loss).",
)
ANCHOR_SUBMIT_DURATION_SECONDS = Histogram(
    "anchor_submit_duration_seconds", "Time from tx submit to confirmed receipt"
)
IDEMPOTENCY_KEYS_PURGED_TOTAL = Counter(
    "idempotency_keys_purged_total",
    "idempotency_keys rows deleted after exceeding config.idempotency_key_retention_seconds "
    "(plan §14.3's 24h policy) — db/idempotency.py::purge_expired, run once per anchor_worker "
    "poll cycle (anchor_worker/main.py). A steadily climbing rate here is expected and "
    "healthy (proof the sweep is running); it staying at zero for a live deployment "
    "handling real Idempotency-Key traffic for more than a day is the actual signal "
    "something's wrong with the sweep itself, not with the data.",
)
ANCHOR_GAS_CEILING_BREACHED_TOTAL = Counter(
    "anchor_gas_ceiling_breached_total",
    "Batches skipped (returned to the outbox as pending, not submitted) because their "
    "owning org's real cumulative gas spend (organizations.gas_spent_wei) has reached its "
    "configured organizations.gas_budget_wei ceiling — plan §11.4's 'hard gas-spend ceiling "
    "... circuit breaker that suspends anchoring on breach', per-org, not global. The "
    "skipped steps stay in the outbox (never lost) and are retried on every subsequent poll "
    "until an operator raises the budget or the org's plan changes — a steadily climbing "
    "rate here for one org, with no operator action, is expected and correct, not a bug.",
    ["org_id"],
)
ANCHOR_WALLET_BALANCE_WEI = Gauge(
    "anchor_wallet_balance_wei",
    "Native-token balance of the anchor worker's signing wallet (sampled each work loop) — "
    "the wallet running dry is exactly the 'gas exhaustion' failure mode "
    "(see tests/test_chaos.py's insufficient-funds scenario): every anchorBatch "
    "call after that silently fails at the pre-send stage, not on-chain, so "
    "there's no revert to alert on without this. Precision note: Prometheus "
    "Gauges are float64 (the exposition format itself is), so this loses "
    "precision below ~1 part in 2^53 of a wei-scale balance — irrelevant for "
    "an alerting threshold (AnchorWalletBalanceLow), not a source of truth "
    "for exact accounting.",
)
ANCHOR_BATCH_GAS_COST_WEI = Histogram(
    "anchor_batch_gas_cost_wei",
    "Real cost (gas_used * effectiveGasPrice, read straight off each confirming batch's own "
    "transaction receipt — see anchor_worker/submit.py, never estimated) of one confirmed "
    "anchorBatch() call, attributed to the org whose steps it anchored. Distinct from "
    "anchor_wallet_balance_wei (a point-in-time balance, not a per-tx cost) and from "
    "anchor_gas_ceiling_breached_total (a skip EVENT, not the spend itself) — this is the "
    "metric that answers 'how much is anchoring actually costing, and for which org' over "
    "time. Same underlying numbers already written to organizations.gas_spent_wei "
    "(db/tenancy.py::record_gas_spend) and returned by GET /gas-spend, just as a Histogram "
    "here instead of a running total, for rate/percentile queries Postgres isn't suited to.",
    ["org_id"],
    buckets=(1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19),  # ~0.00001 to ~10 native-token units
)
ANCHOR_GAS_PRICE_WEI = Histogram(
    "anchor_gas_price_wei",
    "Real effectiveGasPrice (or the tx's own gasPrice, on a chain that doesn't surface "
    "effectiveGasPrice — see submit.py's fallback) per confirmed anchorBatch() call — "
    "network fee-market trend over time, independent of anchor_batch_gas_cost_wei (which "
    "also varies with batch size/step count, not just price). A rising trend here across "
    "many batches, even without any single alert threshold, is the signal that "
    "config.py's anchor_rbf_fee_bump_fraction/priority-fee defaults may need retuning for "
    "current network conditions.",
    buckets=(1e8, 5e8, 1e9, 5e9, 1e10, 5e10, 1e11),  # ~0.1 to ~100 gwei
)

# ── RPC resilience (blockchain/resilient_provider.py) — anchor worker and
#    indexer processes, whichever process's Web3 is built with
#    FallbackHTTPProvider (i.e. *_RPC_FALLBACK_URLS is actually set) ───────
RPC_CIRCUIT_BREAKER_OPEN = Gauge(
    "rpc_circuit_breaker_open",
    "1 if this RPC endpoint's circuit breaker is open (skipped, cooling "
    "down after repeated failures), 0 otherwise — see "
    "blockchain/resilient_provider.py. Only emitted when multiple RPC "
    "endpoints are configured; absent entirely for a single-endpoint setup.",
    ["endpoint"],
)
RPC_CALL_FAILURES_TOTAL = Counter(
    "rpc_call_failures_total",
    "Individual RPC call failures against one endpoint (before failover to the next "
    "configured endpoint, if any) — distinct from rpc_circuit_breaker_open, which is "
    "sampled STATE (is this endpoint currently being skipped), not a failure-event count; "
    "a breaker can stay closed while still accumulating occasional failures below its "
    "failure_threshold, which this counter still captures and that gauge does not. Only "
    "emitted when multiple RPC endpoints are configured (FallbackHTTPProvider) — a "
    "single-endpoint deployment's RPC failures still surface via the higher-level counter "
    "the failing call site already increments (e.g. anchor_batches_failed_total), just "
    "without this endpoint-attributed breakdown.",
    ["endpoint"],
)

# ── Indexer (indexer process) ───────────────────────────────────────────
INDEXER_EVENTS_PROCESSED_TOTAL = Counter(
    "indexer_events_processed_total", "On-chain events processed into the read model", ["event_type"]
)
INDEXER_POLL_LAG_BLOCKS = Gauge(
    "indexer_poll_lag_blocks", "Chain head block number minus indexer's last-processed block"
)
INDEXER_RECONCILIATIONS_TOTAL = Counter(
    "indexer_reconciliations_total",
    "Times reconcile_batch_anchored actually updated a batch stuck at "
    "'submitted' (the anchor-worker-crashed-before-confirming window) — "
    "see indexer/reconcile.py. A rising rate points at the anchor worker "
    "crashing/restarting often, not at the indexer itself.",
)
AGENT_INTEGRITY_VIOLATIONS_TOTAL = Counter(
    "agent_integrity_violations_total",
    "Real AgentIdentityRegistryV2.IntegrityViolation events indexed (indexer/"
    "agent_events.py::index_integrity_violation) — the on-chain tamper alarm firing: a "
    "verifyAgentAndLog() call found the provided config hash didn't match what's "
    "registered for that agent, meaning its model/version/prompt changed without "
    "re-registering (a silent substitution, or a caller passing a stale hash). Already "
    "counted incidentally via indexer_events_processed_total{event_type=\"IntegrityViolation\"} "
    "— this is a purpose-built name a dashboard/alert can target without depending on "
    "that generic counter's label value staying exactly \"IntegrityViolation\". No "
    "project_id/agent_id label (cardinality — see this module's own docstring); per-tenant "
    "drill-down goes through structured logs the same way every other cardinality-sensitive "
    "metric here does. docs/runbooks.md's 'Pausing contracts in an emergency' names "
    "'IntegrityViolation fires unexpectedly at scale' as a trigger condition — this is "
    "what that condition should actually watch (see docker/prometheus/alerts.yml's "
    "AgentIntegrityViolationsDetected).",
)



# ─────────────────────────────────────────────────────────────────────────
#  Phase 3 — continuous integrity monitoring & alerting (integrity_watchdog,
#  notifications/, db/alerts.py, db/orgs.py). No metric here carries a
#  tenant identifier, same rule as every metric above — per-tenant
#  drill-down goes through structured logs instead.
# ─────────────────────────────────────────────────────────────────────────

INTEGRITY_CHECKS_TOTAL = Counter(
    "integrity_checks_total",
    "Detector runs, by outcome — integrity_watchdog's step_rows/merkle_roots/liveness "
    "detectors and the synchronous identity-drift check on POST /steps.",
    ["detector", "result"],  # result: ok | mismatch | missing | error
)
INTEGRITY_ALERTS_RAISED_TOTAL = Counter(
    "integrity_alerts_raised_total", "Alerts raised via db/alerts.py::raise_alert (new OR recurring)",
    ["alert_type", "severity"],
)
WATCHDOG_SWEEP_DURATION_SECONDS = Histogram(
    "watchdog_sweep_duration_seconds", "One detector's one pass over its current tier's batch of work",
    ["detector", "tier"],  # tier: hot | rolling
)
WATCHDOG_CURSOR_LAG_ITEMS = Gauge(
    "watchdog_cursor_lag_items", "Rows behind the current max id — how far the rolling sweep's cursor trails 'now'",
    ["detector"],
)
WATCHDOG_LAST_SUCCESS_TIMESTAMP = Gauge(
    "watchdog_last_success_timestamp", "Unix time of each detector's last successful run — "
    "docker/prometheus/alerts.yml's TrustChainWatchdogSilent watches this for absence, "
    "since a suppressed watchdog raising zero alerts looks identical to 'nothing is wrong' "
    "from every other metric here.",
    ["detector"],
)
WATCHDOG_FULL_SWEEP_AGE_SECONDS = Gauge(
    "watchdog_full_sweep_age_seconds", "Seconds since the rolling tier last completed a full wrap over all history",
)
ALERT_DELIVERIES_TOTAL = Counter(
    "alert_deliveries_total", "notifications/sender.py delivery attempts, by outcome", ["channel", "status"],
)
ALERT_DELIVERY_QUEUE_DEPTH = Gauge(
    "alert_delivery_queue_depth", "alert_deliveries rows currently pending or claimed",
)
ALERT_DELIVERY_LATENCY_SECONDS = Histogram(
    "alert_delivery_latency_seconds", "Time from alert_deliveries row creation to a successful send",
    ["channel"],
)
OPEN_ALERTS = Gauge(
    "open_alerts", "Platform-wide open alert count, by severity — no tenant label, see module note above",
    ["severity"],
)
INVITATIONS_TOTAL = Counter(
    "invitations_total", "Invitation lifecycle events", ["action"],  # created | accepted | revoked | expired
)
MEMBERSHIP_CHANGES_TOTAL = Counter(
    "membership_changes_total", "Membership lifecycle events", ["action"],  # added | role_changed | removed
)
MEMBERSHIP_CACHE_TOTAL = Counter(
    "membership_cache_total",
    "auth.py's Redis-cached membership liveness check outcomes (Phase 3 §4.3) — a rising "
    "miss rate under steady traffic points at TTL churn, not correctness; 'invalidated' "
    "counts explicit deletes on role change/removal, the path that makes revocation "
    "effectively immediate rather than bounded only by the TTL.",
    ["result"],  # hit | miss | invalidated
)


def start_metrics_server(port: int) -> None:
    """For non-HTTP processes (anchor worker, indexer) only — the API
    exposes /metrics through its own FastAPI router instead (main.py),
    since it already has an HTTP server. Starts a stdlib http.server in a
    background thread (prometheus_client's own implementation) — safe to
    call once at process startup alongside an asyncio event loop."""
    from prometheus_client import start_http_server
    start_http_server(port)
    logger.info("metrics_server_started", port=port)


# ─────────────────────────────────────────────────────────────────────────────
#  Sentry
# ─────────────────────────────────────────────────────────────────────────────

def init_sentry(dsn: str, environment: str, traces_sample_rate: float, release: str = "") -> bool:
    """Returns True if Sentry was actually initialized (dsn non-empty),
    False if this was a no-op. Call once, near process startup, before
    any code that might raise.

    What's verified here vs not: sentry_sdk.init() with a well-formed DSN
    succeeds and installs its exception hooks regardless of whether that
    DSN's project actually exists or is reachable — sentry_sdk's own
    background transport thread swallows delivery failures (a network
    error talking to Sentry's ingest API never raises in caller code,
    by design, since observability must never be able to break the thing
    it's observing). tests/test_observability.py exercises this with a
    syntactically-valid-but-fake DSN and confirms init doesn't raise and
    capture_exception doesn't raise either. Actual event delivery to a
    real Sentry project has NOT been verified — this repo has no Sentry
    account to verify against."""
    if not dsn:
        logger.info("sentry_disabled", reason="SENTRY_DSN not set")
        return False

    import sentry_sdk
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        release=release or None,
    )
    logger.info("sentry_initialized", environment=environment, traces_sample_rate=traces_sample_rate)
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  OpenTelemetry tracing
# ─────────────────────────────────────────────────────────────────────────────

def init_tracing(service_name: str, otlp_endpoint: str, fastapi_app=None) -> bool:
    """Returns True if a real exporter was wired up (otlp_endpoint
    non-empty), False if tracing stays a local-only no-op. Either way,
    spans created via `tracer.start_as_current_span(...)` (see
    agents/pipeline.py) work identically in caller code — the only
    difference is whether they go anywhere.

    Verified locally against a real Jaeger instance (docker run
    jaegertracing/all-in-one, OTLP gRPC on :4317) — see
    tests/test_observability.py's module docstring for exactly what was
    checked (spans queryable via Jaeger's own HTTP API, not just "the
    exporter didn't throw")."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    exported = False
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)))
        exported = True

    trace.set_tracer_provider(provider)

    if fastapi_app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(fastapi_app, tracer_provider=provider)

    logger.info("tracing_initialized", service_name=service_name, exporting=exported)
    return exported


def get_tracer(name: str):
    from opentelemetry import trace
    return trace.get_tracer(name)
