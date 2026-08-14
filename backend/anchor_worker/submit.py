"""
anchor_worker/submit.py — signs and submits one AnchorBatch's root on-chain.

Runs synchronously to a receipt (bounded by `confirm_timeout`) rather than
firing and moving on to the next batch: with a single hot signing key, the
worker must not build a second transaction before the first either
confirms or is known to have failed, or it risks nonce collisions. See
blockchain/client.py's BlockchainBridge for the equivalent V1 concern
(there, solved with an explicit pending-nonce counter across concurrent
callers — the anchor worker's batches are submitted one at a time by a
single process instead, so a plain "pending" nonce read per submission is
enough).
"""

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3
from web3.exceptions import TimeExhausted, TransactionNotFound

import observability
from logging_config import get_logger

logger = get_logger(__name__)


class SubmitError(Exception):
    """Non-retriable-as-is failure (revert, or confirmation timeout). The
    caller dead-letters or requeues based on the outbox rows' attempt
    count — see anchor_worker/main.py::handle_submit_failure. Retrying an
    identical batch after a revert would just revert identically
    (DuplicateRoot), so the batch itself is abandoned; only its steps are
    requeued, to be rebatched fresh next round."""


async def submit_batch(
    session: AsyncSession,
    batch: dict,
    contract,
    signer,
    w3: Web3,
    confirm_timeout: int = 60,
) -> dict:
    fn = contract.functions.anchorBatch(batch["run_id_hash"], batch["root_hex"], batch["step_count"])
    submit_start = time.monotonic()

    # Everything from nonce fetch through send is one failure domain: a
    # revert-on-send (e.g. insufficient funds, an RPC hiccup, a stale
    # nonce) has to reach the caller as the same SubmitError a post-send
    # revert would, or it'd propagate as a raw exception instead — main.py's
    # run_once has no handler for that, so the batch would stay stuck at
    # status='building' forever instead of being detached and retried.
    try:
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, signer.address, "pending")
        try:
            latest = await asyncio.to_thread(w3.eth.get_block, "latest")
            base_fee = latest.get("baseFeePerGas")
        except Exception:
            base_fee = None

        tx_params = {"from": signer.address, "nonce": nonce, "gas": 300_000}
        if base_fee is not None:
            priority_fee = w3.to_wei(1, "gwei")
            tx_params["maxPriorityFeePerGas"] = priority_fee
            tx_params["maxFeePerGas"] = base_fee * 2 + priority_fee
        else:
            tx_params["gasPrice"] = await asyncio.to_thread(lambda: w3.eth.gas_price)

        tx = fn.build_transaction(tx_params)
        signed = signer.sign_transaction(tx)
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
    except Exception as e:
        observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="build_or_send").inc()
        raise SubmitError(f"failed to submit batch {batch['batch_id']}: {e}") from e
    tx_hash_hex = "0x" + tx_hash.hex()

    logger.info("batch_submitted", batch_id=batch["batch_id"], tx_hash=tx_hash_hex, nonce=nonce)

    await session.execute(
        text("""
            UPDATE anchor_batches SET status = 'submitted', tx_hash = :tx_hash, submitted_at = :now
            WHERE id = :batch_id
        """),
        {"tx_hash": tx_hash_hex, "now": _now(), "batch_id": batch["batch_id"]},
    )
    await session.commit()

    try:
        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, confirm_timeout)
    except (TimeExhausted, TransactionNotFound) as e:
        observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="timeout").inc()
        raise SubmitError(f"tx {tx_hash_hex} not confirmed within {confirm_timeout}s: {e}") from e

    if receipt.status != 1:
        observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="revert").inc()
        raise SubmitError(f"tx {tx_hash_hex} reverted (status=0)")

    await session.execute(
        text("""
            UPDATE anchor_batches
            SET status = 'confirmed', block_number = :block_number, confirmed_at = :now
            WHERE id = :batch_id
        """),
        {"block_number": receipt.blockNumber, "now": _now(), "batch_id": batch["batch_id"]},
    )
    await session.execute(
        text("UPDATE anchor_outbox SET status = 'anchored' WHERE batch_id = :batch_id"),
        {"batch_id": batch["batch_id"]},
    )
    await session.commit()

    observability.ANCHOR_BATCHES_SUBMITTED_TOTAL.inc()
    observability.ANCHOR_BATCH_SIZE_STEPS.observe(batch["step_count"])
    observability.ANCHOR_SUBMIT_DURATION_SECONDS.observe(time.monotonic() - submit_start)

    return {"tx_hash": tx_hash_hex, "block_number": receipt.blockNumber, "status": "confirmed"}


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
