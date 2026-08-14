"""
Integration tests for the indexer — run against a REAL Postgres and a REAL
Anvil instance with V2 contracts deployed (see conftest.py's
`chain_settings`/`requires_anvil`, shared with test_anchor_worker.py).

Two things get real coverage here that a mock couldn't: (1) that
TrustScoreRegistryV2's ScoreUpdated event, after the fix removing
`indexed` from its string params, actually decodes readable agentId/runId
via a live contract call+event fetch — the whole reason that fix mattered
is unobservable without decoding a real log; and (2) that the indexer
recovers a batch stuck at status='submitted' the same way the anchor
worker's own confirmation path already covers 'building'/failed batches
(test_anchor_worker.py's reaper tests) — the crash window between "tx
sent" and "worker records its own confirmation" that only chain-truth
reconciliation can close.
"""

import asyncio
import time
import uuid

from sqlalchemy import text

import db
from agents.base import log_step
from anchor_worker import chain as chain_module
from anchor_worker.batch import build_batches
from anchor_worker.claim import claim_batch
from anchor_worker.submit import submit_batch
from db.engine import get_sessionmaker
from indexer import chain as indexer_chain_module
from indexer.main import run_once
from tests.conftest import requires_anvil, seed_project


def run(coro):
    return asyncio.run(coro)


def _unique_run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _seed_run_with_steps(run_id: str, n: int) -> list[dict]:
    run(db.create_run(run_id, seed_project(), "indexer test task", None, int(time.time())))
    events = []
    for i in range(n):
        _, evt = run(log_step(
            bridge=None, agent_id="researcher", action="task_received",
            input_text=f"input {i}", output_text=f"output {i}",
            step_index=i, run_id=run_id,
        ))
        events.append(evt)
    return events


@requires_anvil
def test_indexer_reconciles_a_batch_stuck_at_submitted(chain_settings):
    """Simulates the exact crash window reconcile.py exists for: a batch
    whose transaction was actually sent, mined, and confirmed on-chain,
    but whose local row never advanced past 'submitted' — as if the
    worker process died between send and its own receipt-driven update.
    Built by calling submit_batch for real, then manually reverting the
    row's status backward (submit_batch itself can't be interrupted
    mid-flight from a test), which reproduces the identical end state a
    real crash would leave behind."""
    run_id = _unique_run_id("run_indexer_reconcile_test")
    _seed_run_with_steps(run_id, 3)

    contract = chain_module.get_audit_log_contract()
    signer = chain_module.get_signer()
    w3 = chain_module.get_w3()

    async def _claim_batch_submit():
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-a", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        batch = batches[0]
        async with get_sessionmaker()() as session:
            result = await submit_batch(session, batch, contract, signer, w3, confirm_timeout=30)
        return batch, result

    batch, result = run(_claim_batch_submit())
    assert result["status"] == "confirmed"

    # Roll the row back to where a crashed-after-send worker would have
    # left it: 'submitted', with the block/confirmation fields it never
    # got to record, and its outbox rows still 'claimed' rather than
    # 'anchored' — even though the transaction genuinely succeeded.
    async def _simulate_crash_after_send():
        async with get_sessionmaker()() as session:
            await session.execute(
                text("""
                    UPDATE anchor_batches
                    SET status = 'submitted', block_number = NULL, confirmed_at = NULL
                    WHERE id = :batch_id
                """),
                {"batch_id": batch["batch_id"]},
            )
            await session.execute(
                text("UPDATE anchor_outbox SET status = 'claimed' WHERE batch_id = :batch_id"),
                {"batch_id": batch["batch_id"]},
            )
            await session.commit()

    run(_simulate_crash_after_send())

    # Point the indexer at the same Anvil via chain_settings, and run one
    # real poll iteration — it should discover the real BatchAnchored
    # event this submission emitted and fix the row up from chain truth.
    handled = run(run_once())
    assert handled >= 1

    async def _fetch_state():
        from db.models import AnchorBatch, AnchorOutbox
        async with get_sessionmaker()() as session:
            reconciled_batch = await session.get(AnchorBatch, batch["batch_id"])
            from sqlalchemy import select
            outboxes = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.batch_id == batch["batch_id"])
            )).scalars().all()
            return reconciled_batch, outboxes

    reconciled_batch, outboxes = run(_fetch_state())
    assert reconciled_batch.status == "confirmed"
    assert reconciled_batch.block_number is not None
    assert reconciled_batch.confirmed_at is not None
    assert reconciled_batch.tx_hash == result["tx_hash"]
    assert all(o.status == "anchored" for o in outboxes)


@requires_anvil
def test_indexer_indexes_score_updated_with_readable_agent_and_run_ids(chain_settings):
    """The real regression test for the indexed-string bug: agentId/runId
    must come back as actual readable strings from a live event fetch, not
    as unrecoverable keccak256 hashes — which is exactly what would happen
    if `indexed` were still on those event params."""
    contract = indexer_chain_module.get_trust_score_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    run_id = _unique_run_id("run_score_test")
    agent_id = "researcher"
    reason = "integration_test_scoring"

    nonce = w3.eth.get_transaction_count(signer.address, "pending")
    fn = contract.functions.updateScore(agent_id, run_id, 87, reason)
    tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": 400_000, "gasPrice": w3.eth.gas_price})
    signed = signer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    handled = run(run_once())
    assert handled >= 1

    async def _fetch():
        from sqlalchemy import select
        from db.models import ReadModelScore
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(ReadModelScore).where(ReadModelScore.run_id == run_id)
            )).scalars().all()
            return rows

    rows = run(_fetch())
    assert len(rows) == 1
    row = rows[0]
    assert row.agent_id == agent_id
    assert row.run_id == run_id
    assert row.score == 87
    assert row.reason == reason
    assert row.tx_hash == "0x" + tx_hash.hex()


@requires_anvil
def test_indexer_is_idempotent_on_rerun(chain_settings):
    """Running the indexer twice in a row with nothing new in between must
    not double-process anything it already saw — the second run should
    report 0 handled, and rm_scores must not gain a duplicate row (the
    unique constraint on (tx_hash, log_index) is the actual guarantee;
    this proves the indexer relies on it correctly via ON CONFLICT rather
    than erroring out)."""
    contract = indexer_chain_module.get_trust_score_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    run_id = _unique_run_id("run_idempotent_test")
    nonce = w3.eth.get_transaction_count(signer.address, "pending")
    fn = contract.functions.updateScore("validator", run_id, 42, "idempotency_check")
    tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": 400_000, "gasPrice": w3.eth.gas_price})
    signed = signer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    first = run(run_once())
    second = run(run_once())

    assert first >= 1
    assert second == 0

    async def _count():
        from sqlalchemy import select, func
        from db.models import ReadModelScore
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(func.count()).select_from(ReadModelScore).where(ReadModelScore.run_id == run_id)
            )
            return result.scalar_one()

    assert run(_count()) == 1


@requires_anvil
def test_indexer_run_once_samples_circuit_breaker_state(chain_settings, monkeypatch):
    """indexer/main.py::run_once must publish each configured RPC
    endpoint's real circuit-breaker state into
    observability.RPC_CIRCUIT_BREAKER_OPEN every iteration — what
    RpcCircuitBreakerOpen (docker/prometheus/alerts.yml) alerts on."""
    import observability
    from web3 import Web3

    from blockchain.resilient_provider import FallbackHTTPProvider
    from indexer import main as indexer_main

    dead_rpc = "http://localhost:19999"
    anvil_rpc = "http://localhost:8545"
    fallback_w3 = Web3(FallbackHTTPProvider([dead_rpc, anvil_rpc], failure_threshold=1, reset_timeout_seconds=30))
    monkeypatch.setattr(indexer_main, "get_w3", lambda: fallback_w3)

    run(run_once())

    dead_state = observability.RPC_CIRCUIT_BREAKER_OPEN.labels(endpoint=dead_rpc)._value.get()
    healthy_state = observability.RPC_CIRCUIT_BREAKER_OPEN.labels(endpoint=anvil_rpc)._value.get()
    assert dead_state == 1.0  # tripped after the first real connection-refused failure
    assert healthy_state == 0.0
