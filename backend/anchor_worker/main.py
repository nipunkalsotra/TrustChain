"""
anchor_worker/main.py — the anchor worker's process loop.

Run with `python -m anchor_worker.main`. Each iteration: reap stale
claims, claim a batch of pending outbox rows, group them into per-run
Merkle batches, submit each batch on-chain, sleep, repeat. Designed to be
killed at any point (SIGKILL, crash) and resumed by a fresh process —
reap_stale_claims is what makes that recovery actually work rather than
just hoping nothing was mid-flight.
"""

import asyncio
import signal
import socket
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

import observability
from anchor_worker.batch import build_batches
from anchor_worker.chain import get_audit_log_contract, get_signer, get_w3
from anchor_worker.claim import claim_batch
from anchor_worker.reaper import reap_stale_claims
from anchor_worker.submit import SubmitError, submit_batch
from config import get_settings
from db.engine import get_sessionmaker
from logging_config import get_logger

logger = get_logger(__name__)


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


async def handle_submit_failure(session, batch: dict, error: Exception, max_attempts: int) -> None:
    """A batch's on-chain submission failed permanently (revert or
    confirmation timeout) — the batch itself is abandoned (retrying an
    identical root would just revert identically), so its steps are
    detached from it and either requeued for rebatching or dead-lettered,
    depending on how many attempts they have left."""
    now = int(datetime.now(timezone.utc).timestamp())
    logger.error("batch_submit_failed", batch_id=batch["batch_id"], error=str(error))

    await session.execute(
        text("UPDATE anchor_batches SET status = 'failed', last_error = :err WHERE id = :batch_id"),
        {"err": str(error), "batch_id": batch["batch_id"]},
    )
    await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'dead_letter', last_error = :err, batch_id = NULL
            WHERE batch_id = :batch_id AND attempts >= :max_attempts
        """),
        {"err": str(error), "batch_id": batch["batch_id"], "max_attempts": max_attempts},
    )
    await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'pending', batch_id = NULL, claimed_by = NULL, claimed_at = NULL,
                next_attempt_at = :now, last_error = :err
            WHERE batch_id = :batch_id AND attempts < :max_attempts
        """),
        {"now": now, "err": str(error), "batch_id": batch["batch_id"], "max_attempts": max_attempts},
    )
    await session.execute(
        text("UPDATE steps SET anchor_batch_id = NULL WHERE anchor_batch_id = :batch_id"),
        {"batch_id": batch["batch_id"]},
    )
    await session.commit()


async def run_once(worker_id: str, settings) -> int:
    """One iteration: reap, claim, batch, submit. Returns the number of
    outbox rows successfully anchored this round (0 if nothing was
    pending, or everything pending failed to submit)."""
    session_factory = get_sessionmaker()
    w3 = get_w3()
    signer = get_signer()
    contract = get_audit_log_contract()

    async with session_factory() as session:
        reaped = await reap_stale_claims(session, settings.anchor_claim_timeout_seconds, settings.anchor_max_attempts)
    if reaped["reset"] or reaped["dead_lettered"]:
        logger.info("reaper_ran", reset=len(reaped["reset"]), dead_lettered=len(reaped["dead_lettered"]))

    async with session_factory() as session:
        pending_count = (
            await session.execute(text("SELECT COUNT(*) FROM anchor_outbox WHERE status = 'pending'"))
        ).scalar_one()
    observability.ANCHOR_OUTBOX_PENDING.set(pending_count)

    async with session_factory() as session:
        claimed = await claim_batch(session, worker_id, settings.anchor_max_batch_size)
    if not claimed:
        return 0
    logger.info("outbox_claimed", worker_id=worker_id, count=len(claimed))

    async with session_factory() as session:
        batches = await build_batches(session, claimed)

    anchored = 0
    for batch in batches:
        async with session_factory() as session:
            try:
                result = await submit_batch(session, batch, contract, signer, w3)
                logger.info(
                    "batch_confirmed",
                    batch_id=batch["batch_id"],
                    tx_hash=result["tx_hash"],
                    block=result["block_number"],
                )
                anchored += batch["step_count"]
            except SubmitError as e:
                await handle_submit_failure(session, batch, e, settings.anchor_max_attempts)

    return anchored


async def main() -> None:
    settings = get_settings()
    worker_id = make_worker_id()
    logger.info("anchor_worker_starting", worker_id=worker_id)
    observability.start_metrics_server(settings.anchor_worker_metrics_port)
    observability.init_sentry(settings.sentry_dsn, settings.environment, settings.sentry_traces_sample_rate)

    # F14 (Phase 2 plan's fix list): drain in-flight work on SIGTERM,
    # never abandon a submitted-but-unconfirmed transaction without
    # recording it. The shutdown flag is only ever checked BETWEEN full
    # run_once() calls, never during one — a claim that's already in
    # flight always runs to its natural conclusion (confirmed, or handed
    # to handle_submit_failure) before the loop exits. This is on top of,
    # not instead of, the reaper (anchor_worker/reaper.py): the reaper
    # covers a hard kill (SIGKILL, OOM, crash) this handler can't catch;
    # graceful shutdown covers the much more common "orchestrator asked
    # nicely" case without even needing the reaper's timeout to elapse.
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    while not shutdown_event.is_set():
        try:
            anchored = await run_once(worker_id, settings)
        except Exception:
            logger.exception("anchor_worker_iteration_failed")
            anchored = 0
        if anchored == 0 and not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=settings.anchor_poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    logger.info("anchor_worker_shutting_down", worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(main())
