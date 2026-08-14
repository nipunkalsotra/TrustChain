"""
logging_config.py — structured JSON logging with request/run correlation.

Replaces the Phase 1 `logging.basicConfig(format="%(levelname)s %(name)s %(message)s")`
plain-text setup with structlog, so every log line carries a request_id (and,
where relevant, run_id) that ties a request to everything it caused — the
outbox row it wrote, the batch the anchor worker put it in, the indexer
event that confirmed it. That correlation is what makes "why is run_x stuck"
answerable from logs alone instead of by reading code.
"""

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_run_id_var: ContextVar[str] = ContextVar("run_id", default="")


def bind_run_id(run_id: str) -> None:
    """Call from within a request/task to attach run_id to subsequent log lines."""
    _run_id_var.set(run_id)


def _add_correlation_ids(logger, method_name, event_dict):
    request_id = _request_id_var.get()
    run_id = _run_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    if run_id:
        event_dict["run_id"] = run_id
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
