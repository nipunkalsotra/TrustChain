"""
logging_config.py — structured JSON logging with request/run correlation.

Replaces the Phase 1 `logging.basicConfig(format="%(levelname)s %(name)s %(message)s")`
plain-text setup with structlog, so every log line carries a request_id (and,
where relevant, run_id/project_id/org_id) that ties a request to everything
it caused — the outbox row it wrote, the batch the anchor worker put it in,
the indexer event that confirmed it, and which tenant it all belongs to.
That correlation is what makes "why is run_x stuck" AND "which tenant is
hammering us" answerable from logs alone instead of by reading code.

Also carries trace_id/span_id (_add_trace_context below) from whichever
OpenTelemetry span is active when a log line is emitted — a genuinely
separate mechanism from the contextvar-based IDs above (those are set
explicitly per request/run; trace context comes from OTel's own current-
span machinery, observability.py's tracer.start_as_current_span() call
sites), but the same idea: without it, a log line and the distributed
trace it happened inside of are two disconnected views of the same
event with no way to jump between them.
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Optional

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_run_id_var: ContextVar[str] = ContextVar("run_id", default="")
# F11 ("Structured logging with correlation IDs ... request_id, run_id,
# tenant_id"): this codebase's actual tenant unit is a project (see
# db/tenancy.py) under an organization, not a single "tenant_id" field —
# project_id/org_id together ARE this project's tenant_id, so those are
# the field names emitted, matching every other place tenant identity
# appears in this codebase (RLS's app.current_project_id/
# app.current_org_id GUCs, JWT claims, Principal) rather than introducing
# a synonym nothing else uses.
_project_id_var: ContextVar[Optional[int]] = ContextVar("log_project_id", default=None)
_org_id_var: ContextVar[Optional[int]] = ContextVar("log_org_id", default=None)


def bind_run_id(run_id: str) -> None:
    """Call from within a request/task to attach run_id to subsequent log lines."""
    _run_id_var.set(run_id)


def bind_tenant_context(project_id: Optional[int], org_id: Optional[int]) -> None:
    """Call once a request's Principal/CurrentUser is resolved (auth.py's
    get_current_principal/get_current_user — the same place the RLS
    session context, db/engine.py's current_project_id/current_org_id,
    gets set) to attach tenant identity to every subsequent log line for
    this request: which tenant made a failing call, which tenant a slow
    query belongs to, which tenant an anomalous burst of requests came
    from — all currently unanswerable from logs alone without this."""
    _project_id_var.set(project_id)
    _org_id_var.set(org_id)


def _add_correlation_ids(logger, method_name, event_dict):
    request_id = _request_id_var.get()
    run_id = _run_id_var.get()
    project_id = _project_id_var.get()
    org_id = _org_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    if run_id:
        event_dict["run_id"] = run_id
    if project_id is not None:
        event_dict["project_id"] = project_id
    if org_id is not None:
        event_dict["org_id"] = org_id
    return event_dict


def _add_trace_context(logger, method_name, event_dict):
    """Injects the CURRENT OpenTelemetry span's trace_id/span_id (hex,
    same format Jaeger/any OTLP backend displays) into every log line
    emitted while that span is active — observability.py's three real
    tracer.start_as_current_span() call sites (main.py's pipeline_run,
    anchor_worker/main.py's anchor_batch_submit, indexer/main.py's
    indexer_poll) each already wrap logger calls that had no way to be
    found from the matching trace before this, and vice versa. A no-op
    when nothing is tracing (no OTEL_EXPORTER_OTLP_ENDPOINT configured,
    or simply no span active at this exact log call) — trace.get_current_span()
    always returns a valid (if non-recording) Span object rather than
    raising, and its SpanContext.is_valid is False in exactly that case,
    same shape as the request_id/run_id `if` guards above."""
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def configure_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level.upper(),
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_ids,
        _add_trace_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    root_handler = logging.getLogger().handlers[0]
    root_handler.setFormatter(formatter)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assigns a request_id per HTTP request, propagated onto every log line
    emitted while handling it (via the contextvar above), and echoed back
    as an X-Request-ID response header for client-side correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


def get_logger(name: str = __name__):
    return structlog.get_logger(name)
