"""
tests/test_observability.py — verifies observability.py's Prometheus
metrics, OpenTelemetry tracing, and Sentry wiring.

Metrics: exercised through the real FastAPI app (TestClient), reading
GET /metrics and checking real counters/histograms moved after a real
request — not just "the endpoint returns 200".

Tracing: `init_tracing()` was additionally verified BY HAND against a
real local Jaeger instance (`docker run -p 4317:4317 -p 16686:16686
jaegertracing/all-in-one`) — a span created via `get_tracer(...).
start_as_current_span(...)` with a `run_id` attribute, force-flushed, and
confirmed queryable through Jaeger's own `/api/traces` HTTP endpoint,
attribute and all. That's real infra, not mockable in a committed test
(no Jaeger service in CI), so what's checked HERE is the no-exporter-
configured no-op path (safe default) and that a real OTLP exporter is
constructed without error when an endpoint IS configured — the exact
code path that was hand-verified end-to-end above.

Sentry: same story — real event delivery needs a real Sentry account
this repo doesn't have. What's checked here: init with a syntactically
valid but fake DSN doesn't raise, and capture_exception() against that
fake DSN doesn't raise either (sentry_sdk's transport swallows delivery
failures internally, by design — observability must never be able to
break the thing it's observing).
"""

import pytest
from fastapi.testclient import TestClient

import observability


def test_disabled_by_default_sentry_is_a_noop():
    assert observability.init_sentry("", "test", 0.1) is False


def test_sentry_init_and_capture_with_fake_dsn_does_not_raise():
    fake_dsn = "https://abc123fakepublickey@o000000.ingest.us.sentry.io/0000000"
    assert observability.init_sentry(fake_dsn, "test", 0.1) is True

    import sentry_sdk
    try:
        raise ValueError("deliberate test exception for capture")
    except ValueError:
        event_id = sentry_sdk.capture_exception()
    assert event_id is not None


def test_tracing_without_endpoint_is_a_noop_but_still_creates_spans():
    exported = observability.init_tracing("trustchain-test", "")
    assert exported is False

    tracer = observability.get_tracer("test")
    with tracer.start_as_current_span("noop_span") as span:
        span.set_attribute("run_id", "run_test_noop")
    # No exception is the whole test — a no-op exporter must not make
    # span creation/attribute-setting fail for callers.


def test_tracing_with_endpoint_configures_a_real_otlp_exporter():
    # Doesn't require anything listening on this port — constructing the
    # OTLPSpanExporter and registering it with a BatchSpanProcessor
    # doesn't connect eagerly (gRPC connects lazily, on first export);
    # this checks the wiring succeeds, not delivery (see module docstring
    # for how delivery itself was verified, against a real local Jaeger).
    exported = observability.init_tracing("trustchain-test", "localhost:4317")
    assert exported is True


def test_metrics_endpoint_exposes_prometheus_text_format():
    from main import app
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "http_requests_total" in body
        assert "pipeline_runs_total" in body
        assert "anchor_batches_submitted_total" in body


def test_metrics_middleware_records_real_requests():
    # /ready, not /health: /health calls the real V1 blockchain bridge
    # (returns its wallet address), which requires a real PRIVATE_KEY —
    # present in local dev's .env but deliberately absent in CI (it's a
    # real Monad testnet deployer key, not something to fabricate as a
    # secret). /ready only hard-requires the database — see main.py's own
    # docstring on why the two are split. This test only cares that the
    # metrics middleware recorded a real request; which cheap endpoint it
    # hits doesn't matter.
    from main import app
    with TestClient(app) as client:
        client.get("/ready")
        client.get("/ready")
        metrics_body = client.get("/metrics").text

    # Look for the specific counter line for this method+path+status,
    # not just that the metric family exists — proves the middleware
    # actually incremented it for a real request, not that the family
    # was merely registered.
    matching_lines = [
        line for line in metrics_body.splitlines()
        if line.startswith("http_requests_total{")
        and 'path="/ready"' in line
        and 'status="200"' in line
    ]
    assert matching_lines, f"no http_requests_total line for /ready,200 in:\n{metrics_body}"
    value = float(matching_lines[0].rsplit(" ", 1)[-1])
    assert value >= 2.0


def test_rate_limit_rejection_increments_metric(monkeypatch):
    import rate_limit
    from fastapi import HTTPException

    before = rate_limit.observability.RATE_LIMIT_REJECTIONS_TOTAL.labels(kind="run_agent")._value.get()

    async def _always_exceeded(*args, **kwargs):
        raise rate_limit.RateLimitExceeded(retry_after_seconds=1.0)

    monkeypatch.setattr(rate_limit, "check_rate_limit", _always_exceeded)

    with pytest.raises(HTTPException):
        import asyncio
        asyncio.run(rate_limit.enforce_rate_limit("test:key", 1.0, 1.0))

    after = rate_limit.observability.RATE_LIMIT_REJECTIONS_TOTAL.labels(kind="run_agent")._value.get()
    assert after == before + 1
