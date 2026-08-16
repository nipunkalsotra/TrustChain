"""
blockchain/score_writer.py — synchronous score writes to TrustScoreRegistryV2.

Unlike step anchoring (batched via the outbox + anchor worker, because
step volume justifies batching many writes into one Merkle root),
individual trust scores are cheap, low-volume, one-off writes — 4 per run.
This keeps V1's original synchronous "await the tx" shape scorer.py
already had (see agents/scorer.py), just retargeted at V2's contract and
the Signer abstraction instead of V1's BlockchainBridge.

Reuses anchor_worker.chain's connection/signer/contract singletons rather
than building a third copy — that module is the write-capable V2
connection, not something scoped only to batch anchoring; see its
docstring.
"""

import asyncio

import observability
from anchor_worker.chain import get_signer, get_trust_score_contract, get_w3
from blockchain.gas import build_fee_params, estimate_gas_with_margin
from logging_config import get_logger

logger = get_logger(__name__)


async def write_score(agent_id: str, run_id: str, score: int, reason: str) -> str:
    """Signs, sends, and waits for confirmation of one updateScore() call.
    Returns the tx hash. Raises on revert or confirmation timeout — the
    caller (scorer.py) already treats a failed on-chain score write as
    fatal to the pipeline run, matching V1's behavior, so this doesn't
    swallow errors or retry."""
    tracer = observability.get_tracer(__name__)
    with tracer.start_as_current_span("rpc_call.write_score") as span:
        span.set_attribute("agent_id", agent_id)
        span.set_attribute("run_id", run_id)

        w3 = get_w3()
        signer = get_signer()
        contract = get_trust_score_contract()

        fn = contract.functions.updateScore(agent_id, run_id, score, reason)
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, signer.address, "pending")
        gas = await estimate_gas_with_margin(fn, signer.address, fallback=400_000)
        tx_params = {"from": signer.address, "nonce": nonce, "gas": gas}
        tx_params.update(await build_fee_params(w3))
        tx = fn.build_transaction(tx_params)
        signed = signer.sign_transaction(tx)
        tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)

        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, 60)
        if receipt.status != 1:
            span.set_attribute("error", "reverted")
            raise RuntimeError(f"TrustScoreRegistryV2.updateScore reverted for agent={agent_id} run={run_id}")

        tx_hash_hex = "0x" + tx_hash.hex()
        span.set_attribute("tx_hash", tx_hash_hex)
        span.set_attribute("block_number", receipt.blockNumber)

    logger.info("score_written", agent_id=agent_id, run_id=run_id, score=score, tx_hash=tx_hash_hex)
    return tx_hash_hex
