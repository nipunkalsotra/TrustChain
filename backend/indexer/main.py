"""
indexer/main.py — the indexer's process loop.

Run with `python -m indexer.main`. Each iteration polls both event
streams this phase cares about — AgentAuditLogV2.BatchAnchored (closes
the anchor worker's post-send-crash window; see reconcile.py) and
TrustScoreRegistryV2.ScoreUpdated (populates rm_scores) — and sleeps only
if neither produced anything new, so it drains a backlog quickly after
being down rather than trickling through it at the poll interval.
"""

import asyncio
import signal

import observability
from config import get_settings
from db.engine import get_sessionmaker
from indexer.chain import get_audit_log_contract, get_trust_score_contract, get_w3
from indexer.poll import poll_once
from indexer.reconcile import reconcile_batch_anchored
from indexer.scores import index_score_updated
from logging_config import get_logger

logger = get_logger(__name__)


async def run_once() -> int:
    session_factory = get_sessionmaker()
    w3 = get_w3()

    handled = 0
    async with session_factory() as session:
        handled += await poll_once(
            session, w3, get_audit_log_contract(), "BatchAnchored",
            reconcile_batch_anchored, "AgentAuditLogV2",
        )
    async with session_factory() as session:
        handled += await poll_once(
            session, w3, get_trust_score_contract(), "ScoreUpdated",
            index_score_updated, "TrustScoreRegistryV2",
        )
    return handled


async def main() -> None:
    settings = get_settings()
    logger.info("indexer_starting")
    observability.start_metrics_server(settings.indexer_metrics_port)
    observability.init_sentry(settings.sentry_dsn, settings.environment, settings.sentry_traces_sample_rate)

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
            handled = await run_once()
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
