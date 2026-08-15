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


def _unique_agent_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# Arbitrary but fixed project id for tests that call the contract
# directly (not through the HTTP API, which injects the real
# authenticated principal's project_id) — just needs to be a real
# uint256 the contract's own namespacing key can hash.
_TEST_PROJECT_ID = 424242


def _code_hash_for(config: dict) -> bytes:
    import json

    from web3 import Web3
    return Web3.keccak(text=json.dumps(config, sort_keys=True))


@requires_anvil
def test_indexer_indexes_agent_registered_and_revoked(chain_settings):
    """A real registerAgent() then revokeAgent() call, each producing a
    real on-chain event this must actually decode and store — not just
    invoke without raising. Registered/revoked go through in the same
    test since revokeAgent requires an already-registered agent, and
    registering fresh each time (agentId) means AgentRegistered actually
    fires (registerAgent on an EXISTING id emits AgentUpdated instead,
    per the contract's own branch — see AgentIdentityRegistryV2.sol)."""
    contract = indexer_chain_module.get_identity_registry_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    agent_id = _unique_agent_id("run_indexer_identity_test")
    code_hash = _code_hash_for({"agentId": agent_id, "model": "gpt-4o", "version": "2026-01"})

    def _send(fn, gas):
        nonce = w3.eth.get_transaction_count(signer.address, "pending")
        tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": gas, "gasPrice": w3.eth.gas_price})
        signed = signer.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return "0x" + tx_hash.hex()

    register_tx_hash = _send(
        contract.functions.registerAgent(_TEST_PROJECT_ID, agent_id, code_hash, "gpt-4o", "2026-01"), 400_000
    )
    revoke_tx_hash = _send(contract.functions.revokeAgent(_TEST_PROJECT_ID, agent_id), 200_000)

    handled = run(run_once())
    assert handled >= 2

    async def _fetch():
        from sqlalchemy import select

        from db.models import ReadModelAgentEvent
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(ReadModelAgentEvent)
                .where(ReadModelAgentEvent.agent_id == agent_id)
                .order_by(ReadModelAgentEvent.id)
            )).scalars().all()
            return rows

    rows = run(_fetch())
    assert [r.event_type for r in rows] == ["AgentRegistered", "AgentRevoked"]

    registered, revoked = rows
    assert registered.project_id == _TEST_PROJECT_ID
    assert registered.actor == signer.address
    assert registered.code_hash == "0x" + code_hash.hex()
    assert registered.tx_hash == register_tx_hash

    assert revoked.project_id == _TEST_PROJECT_ID
    assert revoked.actor == signer.address
    assert revoked.tx_hash == revoke_tx_hash


@requires_anvil
def test_indexer_indexes_integrity_violation(chain_settings):
    """A real verifyAgentAndLog() call with a deliberately WRONG hash
    against a real registered agent — the contract's actual tamper-alarm
    path (distinct from verifyAgentFull, the view-only call
    identity_writer.verify_agent uses for GET /agents/{id}/verify, which
    can't emit events at all)."""
    contract = indexer_chain_module.get_identity_registry_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    agent_id = _unique_agent_id("run_indexer_violation_test")
    real_hash = _code_hash_for({"agentId": agent_id, "model": "gpt-4o", "version": "2026-01"})
    tampered_hash = _code_hash_for({"agentId": agent_id, "model": "gpt-4o", "version": "2099-01"})

    def _send(fn, gas):
        nonce = w3.eth.get_transaction_count(signer.address, "pending")
        tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": gas, "gasPrice": w3.eth.gas_price})
        signed = signer.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return "0x" + tx_hash.hex()

    _send(contract.functions.registerAgent(_TEST_PROJECT_ID, agent_id, real_hash, "gpt-4o", "2026-01"), 400_000)
    violation_tx_hash = _send(
        contract.functions.verifyAgentAndLog(_TEST_PROJECT_ID, agent_id, tampered_hash), 200_000
    )

    handled = run(run_once())
    assert handled >= 1

    async def _fetch():
        from sqlalchemy import select

        from db.models import ReadModelAgentEvent
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(ReadModelAgentEvent).where(
                    ReadModelAgentEvent.agent_id == agent_id,
                    ReadModelAgentEvent.event_type == "IntegrityViolation",
                )
            )).scalars().all()
            return rows

    rows = run(_fetch())
    assert len(rows) == 1
    row = rows[0]
    assert row.project_id == _TEST_PROJECT_ID
    assert row.expected_hash == "0x" + real_hash.hex()
    assert row.provided_hash == "0x" + tampered_hash.hex()
    assert row.actor is None
    assert row.tx_hash == violation_tx_hash


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


@requires_anvil
def test_indexer_finality_gating_defers_recent_events(chain_settings):
    """A real ScoreUpdated event, emitted this block, must NOT be indexed
    while it's still within indexer_finality_confirmations blocks of the
    chain tip — only once enough real blocks have been mined on top of it.
    Proactive gating (indexer/poll.py's `latest_block = chain_tip -
    finality_confirmations`), distinct from cursor.py's reactive
    hash-mismatch rewind: this test proves the event is never even
    fetched in the first place, not that it gets fetched-then-corrected."""
    gated_settings = chain_settings.model_copy(update={"indexer_finality_confirmations": 5})

    contract = indexer_chain_module.get_trust_score_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    run_id = _unique_run_id("run_finality_test")
    nonce = w3.eth.get_transaction_count(signer.address, "pending")
    fn = contract.functions.updateScore("researcher", run_id, 55, "finality_gating_check")
    tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": 400_000, "gasPrice": w3.eth.gas_price})
    signed = signer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    async def _count():
        from sqlalchemy import func, select

        from db.models import ReadModelScore
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(func.count()).select_from(ReadModelScore).where(ReadModelScore.run_id == run_id)
            )
            return result.scalar_one()

    # Still within the 5-block finality window (Anvil auto-mines one block
    # per tx, so the event's block is chain_tip itself right now) -> must
    # be deferred, not indexed.
    run(run_once(gated_settings))
    assert run(_count()) == 0

    # Mine 5 more real blocks (plain 0-value self-transfers — Anvil has no
    # bare evm_mine-N convenience here, and this repo's infra can't set
    # interval mining without disturbing the shared Anvil instance's
    # automine setting for other tests) so the event's block is now
    # exactly finality_confirmations behind the tip.
    for _ in range(5):
        filler_nonce = w3.eth.get_transaction_count(signer.address, "pending")
        filler_tx = {
            "from": signer.address, "to": signer.address, "value": 0,
            "nonce": filler_nonce, "gas": 21_000, "gasPrice": w3.eth.gas_price,
        }
        filler_signed = signer.sign_transaction(filler_tx)
        filler_hash = w3.eth.send_raw_transaction(filler_signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(filler_hash, timeout=30)

    handled_once_final = run(run_once(gated_settings))
    assert handled_once_final >= 1
    assert run(_count()) == 1


@requires_anvil
def test_indexer_rebuilds_read_model_identically_from_genesis(chain_settings):
    """Invariant I6 (db/models.py's ReadModelScore docstring): rm_scores
    and rm_agent_events are a pure function of chain events and must be
    exactly reconstructable by wiping the read model + cursor and
    replaying from genesis — not merely "doesn't error," but genuinely
    produces the same content back."""
    score_contract = indexer_chain_module.get_trust_score_contract()
    identity_contract = indexer_chain_module.get_identity_registry_contract()
    signer = chain_module.get_signer()
    w3 = indexer_chain_module.get_w3()

    def _send(fn, gas):
        nonce = w3.eth.get_transaction_count(signer.address, "pending")
        tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": gas, "gasPrice": w3.eth.gas_price})
        signed = signer.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return "0x" + tx_hash.hex()

    run_id = _unique_run_id("run_i6_test")
    agent_id = _unique_agent_id("run_i6_agent")
    code_hash = _code_hash_for({"agentId": agent_id, "model": "gpt-4o", "version": "2026-01"})

    _send(score_contract.functions.updateScore("researcher", run_id, 73, "i6_replay_check"), 400_000)
    _send(
        identity_contract.functions.registerAgent(_TEST_PROJECT_ID, agent_id, code_hash, "gpt-4o", "2026-01"),
        400_000,
    )

    handled_first_pass = run(run_once())
    assert handled_first_pass >= 2

    async def _snapshot():
        from sqlalchemy import select

        from db.models import ReadModelAgentEvent, ReadModelScore
        async with get_sessionmaker()() as session:
            scores = (await session.execute(
                select(ReadModelScore).where(ReadModelScore.run_id == run_id)
            )).scalars().all()
            agent_events = (await session.execute(
                select(ReadModelAgentEvent).where(ReadModelAgentEvent.agent_id == agent_id)
            )).scalars().all()
            return (
                [(s.agent_id, s.run_id, s.score, s.reason, s.timestamp, s.tx_hash, s.log_index) for s in scores],
                [(e.event_type, e.agent_id, e.actor, e.code_hash, e.timestamp, e.tx_hash, e.log_index)
                 for e in agent_events],
            )

    before_scores, before_agent_events = run(_snapshot())
    assert len(before_scores) == 1
    assert len(before_agent_events) == 1

    # Wipe exactly the I6-covered tables (read model + cursor) — NOT the
    # whole DB (runs/users/etc. aren't part of this invariant and the
    # events above don't even depend on a `runs` row existing) — then
    # replay every stream from its own deploy_block=0 default.
    async def _wipe_read_model_and_cursor():
        async with get_sessionmaker()() as session:
            await session.execute(text("DELETE FROM rm_scores"))
            await session.execute(text("DELETE FROM rm_agent_events"))
            await session.execute(text("DELETE FROM indexer_cursor"))
            await session.commit()
    run(_wipe_read_model_and_cursor())

    handled_replay = run(run_once())
    assert handled_replay >= 2

    after_scores, after_agent_events = run(_snapshot())
    assert after_scores == before_scores
    assert after_agent_events == before_agent_events
