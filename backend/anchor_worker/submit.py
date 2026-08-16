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
from blockchain.gas import estimate_gas_with_margin
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
    rbf_max_attempts: int = 3,
    rbf_fee_bump_fraction: float = 0.15,
) -> dict:
    # metaURI: optional IPFS/Arweave content-address for the batch's full
    # leaf set (AgentAuditLogV2.anchorBatch's 4th param — see its
    # docstring for what this buys: full public verifiability independent
    # of TrustChain's own database staying intact, §8.1's "recommended
    # end state" mitigation for the Merkle-batching data-availability
    # trade-off). Always "" today — actually pinning the leaf set to
    # IPFS/Arweave needs a real pinning service account, which is
    # out of scope here (no such credentials exist for this deployment);
    # wiring one in later is exactly this one line, not a schema or
    # contract change.
    meta_uri = ""
    fn = contract.functions.anchorBatch(batch["run_id_hash"], batch["root_hex"], batch["step_count"], meta_uri)
    submit_start = time.monotonic()

    # Everything from nonce fetch through send is one failure domain: a
    # revert-on-send (e.g. insufficient funds, an RPC hiccup, a stale
    # nonce) has to reach the caller as the same SubmitError a post-send
    # revert would, or it'd propagate as a raw exception instead — main.py's
    # run_once has no handler for that, so the batch would stay stuck at
    # status='building' forever instead of being detached and retried.
    try:
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, signer.address, "pending")
    except Exception as e:
        observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="build_or_send").inc()
        raise SubmitError(f"failed to submit batch {batch['batch_id']}: {e}") from e

    async def _fee_params(previous: dict | None) -> dict:
        # A node's mempool only accepts a same-nonce replacement if its fee
        # is at least rbf_fee_bump_fraction higher than the ALREADY-PENDING
        # tx's fee (go-ethereum's PriceBump rule) — bumping only the
        # priority-fee component isn't enough to satisfy this when the base
        # fee term dominates maxFeePerGas (e.g. base_fee=20gwei*2=40gwei vs
        # a 1gwei priority tip: a 15% priority bump barely moves the total).
        # So each field is bumped directly off the PREVIOUS attempt's own
        # value, then floored against a fresh read of current network
        # conditions — the fresh floor matters too: base fee can drift
        # upward between attempts, and a bump that clears PriceBump but is
        # still below the current base fee would be accepted by the
        # mempool yet never actually get mined.
        # F8: fresh network conditions come from real eth_feeHistory (same
        # source blockchain/gas.py's build_fee_params uses for every other
        # write path) rather than a single latest-block read — the LAST
        # entry in fee_history's baseFeePerGas array is the chain's own
        # projection for the NEXT block, and the reward column gives a
        # real recent-priority-fee sample instead of a fixed 1 gwei guess.
        try:
            history = await asyncio.to_thread(w3.eth.fee_history, 10, "latest", [50])
            base_fee = history["baseFeePerGas"][-1]
        except Exception:
            base_fee = None
            history = None
        if base_fee is not None:
            rewards = sorted(r[0] for r in history["reward"] if r[0] > 0)
            fresh_priority_fee = max(rewards[len(rewards) // 2], w3.to_wei(1, "gwei")) if rewards else w3.to_wei(1, "gwei")
            if previous is not None:
                bumped_priority_fee = int(previous["maxPriorityFeePerGas"] * (1.0 + rbf_fee_bump_fraction))
                priority_fee = max(bumped_priority_fee, fresh_priority_fee)
            else:
                priority_fee = fresh_priority_fee
            fresh_max_fee = base_fee * 2 + priority_fee
            if previous is not None:
                bumped_max_fee = int(previous["maxFeePerGas"] * (1.0 + rbf_fee_bump_fraction))
                max_fee = max(bumped_max_fee, fresh_max_fee)
            else:
                max_fee = fresh_max_fee
            return {"maxPriorityFeePerGas": priority_fee, "maxFeePerGas": max_fee}
        fresh_gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
        if previous is not None:
            gas_price = max(int(previous["gasPrice"] * (1.0 + rbf_fee_bump_fraction)), fresh_gas_price)
        else:
            gas_price = fresh_gas_price
        return {"gasPrice": gas_price}

    # Estimated once, not per RBF attempt: the call data (and so its gas
    # cost) is identical across replace-by-fee retries of the SAME
    # batch — only the fee params change — so re-estimating on every
    # attempt would just be extra RPC round trips for the same answer.
    gas = await estimate_gas_with_margin(fn, signer.address, fallback=300_000)

    receipt = None
    tx_hash_hex = None
    previous_fee_params = None
    for attempt in range(1, rbf_max_attempts + 1):
        try:
            tx_params = {"from": signer.address, "nonce": nonce, "gas": gas}
            fee_params = await _fee_params(previous_fee_params)
            tx_params.update(fee_params)
            previous_fee_params = fee_params
            tx = fn.build_transaction(tx_params)
            signed = signer.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
        except Exception as e:
            observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="build_or_send").inc()
            raise SubmitError(f"failed to submit batch {batch['batch_id']}: {e}") from e
        tx_hash_hex = "0x" + tx_hash.hex()

        if attempt == 1:
            logger.info("batch_submitted", batch_id=batch["batch_id"], tx_hash=tx_hash_hex, nonce=nonce)
        else:
            observability.ANCHOR_BATCHES_REPLACED_TOTAL.inc()
            logger.warning(
                "batch_submit_replaced_by_fee", batch_id=batch["batch_id"], tx_hash=tx_hash_hex,
                nonce=nonce, attempt=attempt,
            )

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
            break
        except (TimeExhausted, TransactionNotFound) as e:
            if attempt >= rbf_max_attempts:
                observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="timeout").inc()
                raise SubmitError(
                    f"tx {tx_hash_hex} not confirmed within {confirm_timeout}s after "
                    f"{rbf_max_attempts} replace-by-fee attempts: {e}"
                ) from e
            # Same nonce, bumped fee, loop again — this IS the
            # replacement, not a giveup-and-requeue.

    if receipt.status != 1:
        observability.ANCHOR_BATCHES_FAILED_TOTAL.labels(reason="revert").inc()
        raise SubmitError(f"tx {tx_hash_hex} reverted (status=0)")

    # anchorBatch() returns the on-chain anchorId AND emits it in
    # BatchAnchored — decode it from the receipt now, synchronously,
    # rather than leaving onchain_anchor_id NULL until the indexer's
    # separate reconciliation pass eventually reprocesses this same event
    # (indexer/reconcile.py — that path exists for the crash-recovery
    # case where THIS process died before reaching this line, not as the
    # only way this column ever gets populated). Anything that needs
    # anchorId right after confirmation (e.g. GET /steps/{id}/proof,
    # which calls the real verifyProof(anchorId, ...) on-chain) would
    # otherwise see NULL for however long the indexer takes to catch up —
    # a real gap this caught, not a hypothetical.
    decoded = contract.events.BatchAnchored().process_receipt(receipt)
    onchain_anchor_id = decoded[0]["args"]["anchorId"] if decoded else None

    # Real gas-spend attribution (see db/models.py's AnchorBatch docstring
    # for the field-level reasoning): effectiveGasPrice is the receipt's
    # own record of what was ACTUALLY paid per gas unit, not the
    # maxFeePerGas ceiling this batch's tx was willing to pay — the two
    # only coincide when the network's base fee happens to eat the whole
    # budget, which isn't the common case. Falls back to the tx's own
    # gasPrice for a chain/receipt that doesn't surface effectiveGasPrice
    # (a legacy, pre-EIP-1559 chain) rather than leaving this NULL.
    gas_price_wei = receipt.get("effectiveGasPrice")
    if gas_price_wei is None:
        sent_tx = await asyncio.to_thread(w3.eth.get_transaction, tx_hash)
        gas_price_wei = sent_tx.get("gasPrice")

    block_hash_hex = "0x" + receipt.blockHash.hex() if receipt.blockHash else None

    await session.execute(
        text("""
            UPDATE anchor_batches
            SET status = 'confirmed', block_number = :block_number, block_hash = :block_hash, confirmed_at = :now,
                onchain_anchor_id = :onchain_anchor_id, gas_used = :gas_used, gas_price_wei = :gas_price_wei
            WHERE id = :batch_id
        """),
        {
            "block_number": receipt.blockNumber, "block_hash": block_hash_hex, "now": _now(), "batch_id": batch["batch_id"],
            "onchain_anchor_id": onchain_anchor_id,
            "gas_used": receipt.gasUsed, "gas_price_wei": gas_price_wei,
        },
    )
    await session.execute(
        text("UPDATE anchor_outbox SET status = 'anchored' WHERE batch_id = :batch_id"),
        {"batch_id": batch["batch_id"]},
    )
    await session.commit()

    observability.ANCHOR_BATCHES_SUBMITTED_TOTAL.inc()
    observability.ANCHOR_BATCH_SIZE_STEPS.observe(batch["step_count"])
    observability.ANCHOR_SUBMIT_DURATION_SECONDS.observe(time.monotonic() - submit_start)
    if gas_price_wei is not None:
        observability.ANCHOR_GAS_PRICE_WEI.observe(gas_price_wei)

    return {
        "tx_hash": tx_hash_hex, "block_number": receipt.blockNumber, "block_hash": block_hash_hex, "status": "confirmed",
        "onchain_anchor_id": onchain_anchor_id,
        # Real cost of THIS confirmation, for the caller to attribute to
        # the owning org's gas-spend ceiling (db/tenancy.py's
        # record_gas_spend) — same values just written to
        # anchor_batches.gas_used/gas_price_wei above, not recomputed.
        "gas_used": receipt.gasUsed, "gas_price_wei": gas_price_wei,
    }


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
