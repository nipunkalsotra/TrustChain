"""
indexer/main.py — the indexer's process loop.

Run with `python -m indexer.main`. Each iteration polls every event stream
this phase cares about — AgentAuditLogV2.BatchAnchored (closes the anchor
worker's post-send-crash window; see reconcile.py), TrustScoreRegistryV2.
ScoreUpdated (populates rm_scores), and AgentIdentityRegistryV2's
AgentRegistered / AgentUpdated / AgentRevoked / IntegrityViolation
(populates rm_agent_events AND the current-state `agents` table, see
agent_events.py) — and sleeps only if none of them produced anything
new, so it drains a backlog quickly after being down rather than
trickling through it at the poll interval.

V1's contracts are deliberately NOT polled here — see docs/adr/0003's
"Indexer scope" section for why (short version: V1 is read-only and
already served by direct on-chain reads, with no query surface that would
need a read model).
"""

import asyncio
import signal

import observability
from blockchain.resilient_provider import sample_breaker_states
from config import get_settings
from db.engine import get_sessionmaker
from indexer.agent_events import (
    index_agent_registered,
    index_agent_revoked,
    index_agent_updated,
    index_integrity_violation,
)
from indexer.chain import (
    get_audit_log_contract,
    get_identity_registry_contract,
    get_trust_score_contract,
    get_w3,
)
from indexer.poll import poll_once
from indexer.reconcile import reconcile_batch_anchored
from indexer.scores import index_score_updated
from logging_config import get_logger

logger = get_logger(__name__)


async def _poll_traced(session_factory, w3, contract, event_name, handler, cursor_name, finality) -> int:
    """poll_once, wrapped in its own span — one per event stream per
    iteration, so a slow/stuck stream (e.g. RPC latency on just
    IntegrityViolation's get_logs call) is visible as its own span in a
    trace, not folded into one opaque 'indexer poll' span covering all
    five streams."""
    tracer = observability.get_tracer(__name__)
    with tracer.start_as_current_span("indexer_poll") as span:
        span.set_attribute("cursor_name", cursor_name)
        span.set_attribute("event_name", event_name)
        async with session_factory() as session:
            handled = await poll_once(
                session, w3, contract, event_name, handler, cursor_name, finality_confirmations=finality,
            )
        span.set_attribute("handled", handled)
        return handled


async def run_once(settings=None) -> int:
    if settings is None:
        settings = get_settings()
    session_factory = get_sessionmaker()
    w3 = get_w3()
    finality = settings.indexer_finality_confirmations

    handled = 0
    handled += await _poll_traced(
        session_factory, w3, get_audit_log_contract(), "BatchAnchored",
        reconcile_batch_anchored, "AgentAuditLogV2", finality,
    )
    handled += await _poll_traced(
        session_factory, w3, get_trust_score_contract(), "ScoreUpdated",
        index_score_updated, "TrustScoreRegistryV2", finality,
    )
    # Four distinct cursor names ("AgentIdentityRegistryV2:<Event>"), even
    # though all four events live on the same contract — poll_once's
    # cursor is keyed on the string passed here (indexer_cursor.contract_name
    # is the primary key), so reusing "AgentIdentityRegistryV2" across all
    # four calls would have each call fast-forward the shared cursor past
    # the block range the NEXT call still needs to scan for its own event
    # type, silently skipping events rather than genuinely tracking each
    # stream's own progress.
    identity_contract = get_identity_registry_contract()
    handled += await _poll_traced(
        session_factory, w3, identity_contract, "AgentRegistered",
        index_agent_registered, "AgentIdentityRegistryV2:AgentRegistered", finality,
    )
    handled += await _poll_traced(
        session_factory, w3, identity_contract, "AgentUpdated",
        index_agent_updated, "AgentIdentityRegistryV2:AgentUpdated", finality,
    )
    handled += await _poll_traced(
        session_factory, w3, identity_contract, "AgentRevoked",
        index_agent_revoked, "AgentIdentityRegistryV2:AgentRevoked", finality,
    )
    handled += await _poll_traced(
        session_factory, w3, identity_contract, "IntegrityViolation",
        index_integrity_violation, "AgentIdentityRegistryV2:IntegrityViolation", finality,
    )

    # Sampled AFTER polling, not before: it's this same iteration's real
    # RPC calls (get_logs for both event streams) that would trip a
    # breaker, so reading breaker state first would only ever reflect the
    # PREVIOUS iteration's activity, one cycle stale.
    for endpoint, state in sample_breaker_states(w3).items():
        observability.RPC_CIRCUIT_BREAKER_OPEN.labels(endpoint=endpoint).set(1 if state == "open" else 0)

    return handled


async def main() -> None:
    settings = get_settings()
    logger.info("indexer_starting")
    observability.start_metrics_server(settings.indexer_metrics_port)
    observability.init_sentry(settings.sentry_dsn, settings.environment, settings.sentry_traces_sample_rate)
    # Distinct service name from the API's own — see anchor_worker/main.py's
    # identical comment on why this can't just reuse settings.otel_service_name.
    observability.init_tracing("trustchain-indexer", settings.otel_exporter_otlp_endpoint)

    # F14 (Phase 2 plan's fix list): drain in-flight work on SIGTERM — same
    # pattern as anchor_worker/main.py. The shutdown flag is only checked
    # between full run_once() calls, so a poll already in progress (which
    # updates a cursor and commits per-contract, not mid-batch) always
    # finishes cleanly before the process actually exits.
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    while not shutdown_event.is_set():
        try:
            handled = await run_once(settings)
        except Exception:
            logger.exception("indexer_iteration_failed")
            handled = 0
        if handled == 0 and not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=settings.indexer_poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    logger.info("indexer_shutting_down")


if __name__ == "__main__":
    asyncio.run(main())
