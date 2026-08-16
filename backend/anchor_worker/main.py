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
from anchor_worker.nonce_lock import NonceAuthorityLock
from anchor_worker.reaper import reap_stale_claims
from anchor_worker.submit import SubmitError, submit_batch
from blockchain.resilient_provider import sample_breaker_states
from config import get_settings
from db import idempotency, tenancy
from db.engine import get_sessionmaker
from logging_config import get_logger

logger = get_logger(__name__)


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


async def handle_submit_failure(
    session, batch: dict, error: Exception, max_attempts: int,
    backoff_base_seconds: float = 5.0, backoff_max_seconds: float = 300.0,
) -> None:
    """A batch's on-chain submission failed permanently (revert or
    confirmation timeout) — the batch itself is abandoned (retrying an
    identical root would just revert identically), so its steps are
    detached from it and either requeued for rebatching or dead-lettered,
    depending on how many attempts they have left.

    Requeued rows get an EXPONENTIAL backoff on next_attempt_at, computed
    per-row from that row's own `attempts` (already incremented at claim
    time — see claim.py), not a flat `now`: an immediate retry after a
    transient failure (RPC hiccup, momentary insufficient funds) just
    re-fails immediately too, burning attempts against max_attempts for
    no benefit. attempts=1 (first failure) -> wait backoff_base seconds;
    attempts=2 -> backoff_base*2; ... capped at backoff_max_seconds so a
    row that's failed many times doesn't wait for days."""
    now = int(datetime.now(timezone.utc).timestamp())
    logger.error("batch_submit_failed", batch_id=batch["batch_id"], error=str(error))

    await session.execute(
        text("UPDATE anchor_batches SET status = 'failed', last_error = :err WHERE id = :batch_id"),
        {"err": str(error), "batch_id": batch["batch_id"]},
    )
    dead_lettered = await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'dead_letter', last_error = :err, batch_id = NULL
            WHERE batch_id = :batch_id AND attempts >= :max_attempts
            RETURNING id
        """),
        {"err": str(error), "batch_id": batch["batch_id"], "max_attempts": max_attempts},
    )
    dead_lettered_count = len(dead_lettered.fetchall())
    if dead_lettered_count:
        observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="submit_failure").inc(dead_lettered_count)
    await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'pending', batch_id = NULL, claimed_by = NULL, claimed_at = NULL,
                next_attempt_at = :now + FLOOR(LEAST(:backoff_base * POWER(2, GREATEST(attempts, 1) - 1), :backoff_max))::bigint,
                last_error = :err
            WHERE batch_id = :batch_id AND attempts < :max_attempts
        """),
        {
            "now": now, "err": str(error), "batch_id": batch["batch_id"], "max_attempts": max_attempts,
            "backoff_base": backoff_base_seconds, "backoff_max": backoff_max_seconds,
        },
    )
    await session.execute(
        text("UPDATE steps SET anchor_batch_id = NULL WHERE anchor_batch_id = :batch_id"),
        {"batch_id": batch["batch_id"]},
    )
    await session.commit()


async def handle_gas_ceiling_skip(session, batch: dict, org_id: int) -> None:
    """A batch's owning org has hit its gas-spend ceiling (plan §11.4's
    circuit breaker) — deliberately NOT treated as a submission failure:
    the steps are returned to 'pending' for the next poll cycle to pick
    up once the ceiling clears (a budget raise, a plan change), same as
    handle_submit_failure's requeue path, but the claim-time attempts
    increment is undone rather than counted toward anchor_max_attempts —
    a step must never get dead-lettered purely because its org hasn't
    topped up its gas budget yet; that's an outbox promise (I1: anchored
    or explicitly dead-lettered, never silently dropped — an org policy
    isn't a step-level failure)."""
    logger.warning(
        "batch_skipped_gas_ceiling", batch_id=batch["batch_id"], org_id=org_id, run_id=batch["run_id"],
    )
    observability.ANCHOR_GAS_CEILING_BREACHED_TOTAL.labels(org_id=str(org_id)).inc()

    await session.execute(
        text("""
            UPDATE anchor_batches SET status = 'failed', last_error = :err WHERE id = :batch_id
        """),
        {"err": f"org {org_id} gas-spend ceiling reached", "batch_id": batch["batch_id"]},
    )
    await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'pending', batch_id = NULL, claimed_by = NULL, claimed_at = NULL,
                attempts = GREATEST(attempts - 1, 0),
                last_error = :err
            WHERE batch_id = :batch_id
        """),
        {"err": f"org {org_id} gas-spend ceiling reached", "batch_id": batch["batch_id"]},
    )
    await session.execute(
        text("UPDATE steps SET anchor_batch_id = NULL WHERE anchor_batch_id = :batch_id"),
        {"batch_id": batch["batch_id"]},
    )
    await session.commit()


async def run_once(worker_id: str, settings) -> int:
    """One iteration: reap, purge expired idempotency keys, claim, batch,
    submit. Returns the number of outbox rows successfully anchored this
    round (0 if nothing was pending, or everything pending failed to
    submit)."""
    session_factory = get_sessionmaker()
    w3 = get_w3()
    signer = get_signer()
    contract = get_audit_log_contract()

    async with session_factory() as session:
        reaped = await reap_stale_claims(session, settings.anchor_claim_timeout_seconds, settings.anchor_max_attempts)
    if reaped["reset"] or reaped["dead_lettered"]:
        logger.info("reaper_ran", reset=len(reaped["reset"]), dead_lettered=len(reaped["dead_lettered"]))

    async with session_factory() as session:
        purged = await idempotency.purge_expired(session, settings.idempotency_key_retention_seconds)
    if purged:
        observability.IDEMPOTENCY_KEYS_PURGED_TOTAL.inc(len(purged))
        logger.info("idempotency_keys_purged", count=len(purged))

    async with session_factory() as session:
        pending_stats = (
            await session.execute(text(
                "SELECT COUNT(*) AS cnt, MIN(created_at) AS oldest FROM anchor_outbox WHERE status = 'pending'"
            ))
        ).first()
    pending_count = pending_stats.cnt
    observability.ANCHOR_OUTBOX_PENDING.set(pending_count)

    # Wallet balance / RPC circuit-breaker telemetry is sampled every
    # poll cycle unconditionally — an operator needs both regardless of
    # whether there happens to be anchoring work to do this round (a
    # wallet running dry, or an RPC endpoint degrading, matters even
    # during a quiet period). Moving this below the flush-or-defer check
    # below would silently stop updating these during any cycle that
    # defers — found by this change's own test suite, which caught
    # test_run_once_samples_real_wallet_balance regressing to a stale
    # value once the (originally unconditional) sampling briefly moved
    # after an early return.
    try:
        balance = await asyncio.to_thread(w3.eth.get_balance, signer.address)
        observability.ANCHOR_WALLET_BALANCE_WEI.set(balance)
    except Exception:
        logger.warning("wallet_balance_sample_failed", exc_info=True)

    for endpoint, state in sample_breaker_states(w3).items():
        observability.RPC_CIRCUIT_BREAKER_OPEN.labels(endpoint=endpoint).set(1 if state == "open" else 0)

    if pending_count == 0:
        return 0

    # Flush now if there's a full batch's worth pending OR the oldest
    # pending row has been waiting past anchor_max_batch_age_seconds —
    # otherwise hold and let more accumulate. Without an age bound, low
    # traffic (a trickle that never reaches anchor_max_batch_size) would
    # wait indefinitely for a batch that never fills; without a size
    # cap, one poll cycle could try to cram an unbounded number of
    # leaves into a single transaction. Every step still gets its own
    # accurate created_at-based age check next poll if it's not flushed
    # this round — nothing is lost, just deferred.
    now = int(datetime.now(timezone.utc).timestamp())
    oldest_age_seconds = now - pending_stats.oldest
    should_flush = (
        pending_count >= settings.anchor_max_batch_size
        or oldest_age_seconds >= settings.anchor_max_batch_age_seconds
    )
    if not should_flush:
        logger.info(
            "batch_age_flush_deferred", pending_count=pending_count,
            oldest_age_seconds=oldest_age_seconds, max_batch_age_seconds=settings.anchor_max_batch_age_seconds,
        )
        return 0

    async with session_factory() as session:
        claimed = await claim_batch(session, worker_id, settings.anchor_max_batch_size)
    if not claimed:
        return 0
    logger.info("outbox_claimed", worker_id=worker_id, count=len(claimed))

    async with session_factory() as session:
        batches = await build_batches(session, claimed)

    tracer = observability.get_tracer(__name__)
    anchored = 0
    for batch in batches:
        # F11.4: hard gas-spend ceiling circuit breaker — checked per
        # batch (an org's spend can cross its ceiling mid-round, between
        # two batches in the same claimed set), before ever building a
        # transaction for it. Resolved fresh each time, not cached across
        # the loop: cheap (one indexed lookup), and a stale cached value
        # would either let one batch too many through right at the
        # boundary or (worse) incorrectly block a batch for an org that
        # had its budget raised moments ago.
        org_id = await tenancy.get_org_id_for_run(batch["run_id"])
        if org_id is not None:
            budget_status = await tenancy.get_org_gas_budget_status(org_id)
            if budget_status["breached"]:
                async with session_factory() as session:
                    await handle_gas_ceiling_skip(session, batch, org_id)
                continue

        async with session_factory() as session:
            with tracer.start_as_current_span("anchor_batch_submit") as span:
                span.set_attribute("batch_id", batch["batch_id"])
                span.set_attribute("run_id", batch["run_id"])
                span.set_attribute("step_count", batch["step_count"])
                try:
                    result = await submit_batch(
                        session, batch, contract, signer, w3,
                        rbf_max_attempts=settings.anchor_rbf_max_attempts,
                        rbf_fee_bump_fraction=settings.anchor_rbf_fee_bump_fraction,
                    )
                    span.set_attribute("tx_hash", result["tx_hash"])
                    span.set_attribute("block_number", result["block_number"])
                    logger.info(
                        "batch_confirmed",
                        batch_id=batch["batch_id"],
                        tx_hash=result["tx_hash"],
                        block=result["block_number"],
                    )
                    anchored += batch["step_count"]
                    if org_id is not None and result["gas_used"] is not None and result["gas_price_wei"] is not None:
                        gas_cost_wei = result["gas_used"] * result["gas_price_wei"]
                        await tenancy.record_gas_spend(org_id, gas_cost_wei)
                        observability.ANCHOR_BATCH_GAS_COST_WEI.labels(org_id=str(org_id)).observe(gas_cost_wei)
                except SubmitError as e:
                    span.set_attribute("error", str(e))
                    await handle_submit_failure(
                        session, batch, e, settings.anchor_max_attempts,
                        settings.anchor_backoff_base_seconds, settings.anchor_backoff_max_seconds,
                    )

    return anchored


async def main() -> None:
    settings = get_settings()
    worker_id = make_worker_id()
    logger.info("anchor_worker_starting", worker_id=worker_id)
    observability.start_metrics_server(settings.anchor_worker_metrics_port)
    observability.init_sentry(settings.sentry_dsn, settings.environment, settings.sentry_traces_sample_rate)
    # A distinct service name from the API's own ("trustchain-api", see
    # config.py's otel_service_name default) — not settings.otel_service_name,
    # which is the API-process-specific field. Without this, every batch
    # submit span would show up under "trustchain-api" in Jaeger/whatever
    # OTLP backend is configured, indistinguishable from the process that
    # actually served the HTTP request that queued the work.
    observability.init_tracing("trustchain-anchor-worker", settings.otel_exporter_otlp_endpoint)

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

    # F6: sole nonce authority, enforced for real — see
    # anchor_worker/nonce_lock.py's module docstring for why SKIP LOCKED
    # on the outbox claim alone doesn't cover this. Blocks HERE, before
    # ever touching the outbox/chain, if another instance already holds
    # it — polling rather than a single indefinite blocking call so
    # SIGTERM during the wait (a replacement instance started before the
    # old one finished draining) exits cleanly instead of hanging.
    nonce_lock = NonceAuthorityLock()
    acquired = False
    logged_waiting = False
    while not shutdown_event.is_set() and not acquired:
        acquired = await nonce_lock.try_acquire()
        if acquired:
            logger.info("anchor_worker_nonce_authority_acquired", worker_id=worker_id)
            break
        if not logged_waiting:
            logger.warning(
                "anchor_worker_waiting_for_nonce_authority", worker_id=worker_id,
                detail="another anchor-worker instance currently holds the nonce-authority lock",
            )
            logged_waiting = True
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=settings.anchor_nonce_lock_poll_seconds)
        except asyncio.TimeoutError:
            pass

    if not acquired:
        # shutdown_event fired before the lock was ever acquired —
        # nothing to release and no work to drain; exit immediately
        # rather than falling through to the main loop without nonce
        # authority.
        logger.info("anchor_worker_shutting_down", worker_id=worker_id)
        return

    try:
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
    finally:
        await nonce_lock.release()
        logger.info("anchor_worker_shutting_down", worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(main())
