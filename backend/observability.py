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
    "rate_limit_rejections_total", "Requests rejected by rate limiting", ["kind"]  # run_agent|login
)

# ── Anchor worker (anchor_worker process) ───────────────────────────────
ANCHOR_BATCHES_SUBMITTED_TOTAL = Counter(
    "anchor_batches_submitted_total", "AnchorBatch transactions successfully confirmed"
)
ANCHOR_BATCHES_FAILED_TOTAL = Counter(
    "anchor_batches_failed_total", "AnchorBatch submissions that failed", ["reason"]
)
ANCHOR_BATCH_SIZE_STEPS = Histogram(
    "anchor_batch_size_steps",
    "Number of steps anchored per batch",
    buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256),
)
ANCHOR_OUTBOX_PENDING = Gauge(
    "anchor_outbox_pending", "Steps in anchor_outbox awaiting a batch (sampled each work loop)"
)
ANCHOR_SUBMIT_DURATION_SECONDS = Histogram(
    "anchor_submit_duration_seconds", "Time from tx submit to confirmed receipt"
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
