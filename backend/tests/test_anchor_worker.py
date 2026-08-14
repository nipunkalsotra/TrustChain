"""
Integration tests for the anchor worker — run against a REAL Postgres
(docker-compose's `postgres` service) and a REAL Anvil instance
(docker-compose's `anvil` service, with V2 contracts already deployed via
`forge script script/DeployV2.s.sol --broadcast`; see
backend/contracts/addresses_v2.json).

Unlike the rest of the suite, these tests do not run against a fake chain
— the entire point of the outbox/anchor-worker design is durability and
cross-language Merkle compatibility, neither of which a mock would
actually exercise. `chain_settings` points anchor_worker.chain's cached
Web3/contract/signer at Anvil instead of get_settings()'s testnet default.

Skipped automatically if Anvil or the deployed V2 addresses aren't
reachable, so the rest of the suite still runs in environments without
docker-compose up. `chain_settings` and `requires_anvil` live in
conftest.py, shared with test_indexer.py.
"""

import asyncio
import time
import uuid

import pytest
from web3 import Web3

import db
from agents.base import log_step
from anchor_worker import chain as chain_module
from anchor_worker.batch import build_batches
from anchor_worker.claim import claim_batch
from anchor_worker.main import handle_submit_failure, run_once
from anchor_worker.reaper import reap_stale_claims
from anchor_worker.submit import SubmitError, submit_batch
from blockchain.merkle import build_tree
from tests.conftest import requires_anvil, seed_project


def run(coro):
    return asyncio.run(coro)


def _unique_run_id(prefix: str) -> str:
    # Postgres is truncated between tests (conftest's isolated_db), but
    # Anvil's chain state is NOT reset between test runs — it's a
    # long-lived container across the whole docker-compose session. A
    # fixed run_id would let a second test invocation collide with a
    # batch a *previous* run already anchored on-chain for that same
    # runIdHash (getBatchesForRun would then return >1 entry). Suffixing
    # with a fresh uuid keeps each invocation's on-chain footprint unique.
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _seed_run_with_steps(run_id: str, n: int) -> list[dict]:
    run(db.create_run(run_id, seed_project(), "anchor worker test task", None, int(time.time())))
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
def test_claim_batch_atomically_claims_pending_outbox_rows(chain_settings):
    run_id = _unique_run_id("run_claim_test")
    events = _seed_run_with_steps(run_id, 3)

    async def _claim():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            return await claim_batch(session, "worker-a", batch_size=10)

    claimed = run(_claim())
    assert len(claimed) == 3
    claimed_step_ids = {c["id"] for c in claimed}
    assert claimed_step_ids == {e["stepId"] for e in events}
    for c in claimed:
        assert c["run_id"] == run_id
        assert c["leaf_hash"].startswith("0x")

    # A second worker claiming right after must see nothing left pending —
    # this is the whole point of FOR UPDATE SKIP LOCKED + status='claimed'.
    async def _claim_again():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            return await claim_batch(session, "worker-b", batch_size=10)

    assert run(_claim_again()) == []


@requires_anvil
def test_build_batches_produces_merkle_root_matching_pure_python(chain_settings):
    run_id = _unique_run_id("run_batch_test")
    events = _seed_run_with_steps(run_id, 5)

    async def _claim_and_batch():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-a", batch_size=10)
        async with get_sessionmaker()() as session:
            return claimed, await build_batches(session, claimed)

    claimed, batches = run(_claim_and_batch())
    assert len(batches) == 1
    batch = batches[0]
    assert batch["run_id"] == run_id
    assert batch["step_count"] == 5

    # Independently rebuild the tree from the same claimed leaves, in the
    # same step_index order build_batches uses, and confirm the roots match
    # — this is the cross-check that build_batches isn't silently reordering
    # or dropping a leaf before handing off to merkle.build_tree.
    ordered = sorted(claimed, key=lambda s: s["step_index"])
    leaves = [bytes.fromhex(s["leaf_hash"].removeprefix("0x")) for s in ordered]
    expected_root = build_tree(leaves).root_hex
    assert batch["root_hex"] == expected_root

    async def _fetch_steps():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import AnchorOutbox, Step
        async with get_sessionmaker()() as session:
            steps = (await session.execute(select(Step).where(Step.run_id == run_id))).scalars().all()
            outboxes = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.step_id.in_([s.id for s in steps]))
            )).scalars().all()
            return steps, outboxes

    steps, outboxes = run(_fetch_steps())
    assert all(s.anchor_batch_id == batch["batch_id"] for s in steps)
    assert all(o.batch_id == batch["batch_id"] for o in outboxes)


@requires_anvil
def test_submit_batch_anchors_on_chain_and_proof_verifies(chain_settings):
    """The real cross-language check: a proof generated by merkle.py for a
    leaf in this batch must verify against AgentAuditLogV2.verifyProof() —
    an actual EVM call against the just-anchored root, not a Python-side
    self-check."""
    run_id = _unique_run_id("run_submit_test")
    _seed_run_with_steps(run_id, 4)

    async def _claim_batch_submit():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-a", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        batch = batches[0]
        contract = chain_module.get_audit_log_contract()
        signer = chain_module.get_signer()
        w3 = chain_module.get_w3()
        async with get_sessionmaker()() as session:
            result = await submit_batch(session, batch, contract, signer, w3, confirm_timeout=30)
        return claimed, batch, result

    claimed, batch, result = run(_claim_batch_submit())
    assert result["status"] == "confirmed"
    assert result["tx_hash"].startswith("0x")
    assert result["block_number"] > 0

    contract = chain_module.get_audit_log_contract()
    total_batches = contract.functions.getTotalBatches().call()
    assert total_batches >= 1

    run_id_hash_bytes = Web3.keccak(text=run_id)
    anchor_ids = contract.functions.getBatchesForRun(run_id_hash_bytes).call()
    assert len(anchor_ids) == 1
    anchor_id = anchor_ids[0]

    on_chain_batch = contract.functions.getBatch(anchor_id).call()
    assert "0x" + on_chain_batch[1].hex() == batch["root_hex"]
    assert on_chain_batch[2] == batch["step_count"]

    # Rebuild the tree exactly as build_batches did, then prove membership
    # of one real leaf against the on-chain root via a live verifyProof() call.
    ordered = sorted(claimed, key=lambda s: s["step_index"])
    leaves = [bytes.fromhex(s["leaf_hash"].removeprefix("0x")) for s in ordered]
    tree = build_tree(leaves)
    proof = tree.proof(0)
    assert contract.functions.verifyProof(anchor_id, leaves[0], proof).call() is True

    # A leaf that was never in this tree must NOT verify.
    forged_leaf = Web3.keccak(text="never anchored")
    assert contract.functions.verifyProof(anchor_id, forged_leaf, proof).call() is False

    async def _fetch_outbox_status():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import AnchorOutbox
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.batch_id == batch["batch_id"])
            )).scalars().all()
            return rows

    outbox_rows = run(_fetch_outbox_status())
    assert len(outbox_rows) == 4
    assert all(o.status == "anchored" for o in outbox_rows)


@requires_anvil
def test_reaper_recovers_claims_orphaned_by_a_crashed_worker(chain_settings):
    """Simulates the chaos scenario without literally SIGKILLing a
    subprocess: claim outbox rows (as if a worker was about to batch them),
    then never call build_batches/submit_batch — exactly what a crash
    between claim and submit leaves behind. Backdate claimed_at past the
    timeout and confirm the reaper resets them to pending rather than
    losing them."""
    run_id = _unique_run_id("run_reaper_test")
    events = _seed_run_with_steps(run_id, 2)

    async def _claim_then_backdate():
        from db.engine import get_sessionmaker
        from sqlalchemy import text
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-crashed", batch_size=10)
        assert len(claimed) == 2

        stale_claimed_at = int(time.time()) - 999
        async with get_sessionmaker()() as session:
            await session.execute(
                text("UPDATE anchor_outbox SET claimed_at = :t WHERE step_id = ANY(:ids)"),
                {"t": stale_claimed_at, "ids": [e["stepId"] for e in events]},
            )
            await session.commit()
        return claimed

    run(_claim_then_backdate())

    async def _reap():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            return await reap_stale_claims(session, claim_timeout_seconds=5, max_attempts=3)

    result = run(_reap())
    assert len(result["reset"]) == 2
    assert result["dead_lettered"] == []

    # Now a fresh worker (run_once, driven through the real settings this
    # fixture built) must be able to reclaim, batch, and confirm these
    # steps on-chain — proving the recovery is end-to-end, not just a
    # status flip in Postgres.
    anchored = run(run_once("worker-recovered", chain_settings))
    assert anchored == 2

    async def _fetch_outbox_status():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import AnchorOutbox
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.step_id.in_([e["stepId"] for e in events]))
            )).scalars().all()
            return rows

    outbox_rows = run(_fetch_outbox_status())
    assert all(o.status == "anchored" for o in outbox_rows)


@requires_anvil
def test_reaper_dead_letters_claims_that_exhausted_attempts(chain_settings):
    run_id = _unique_run_id("run_dead_letter_test")
    events = _seed_run_with_steps(run_id, 1)

    async def _claim_n_times(n):
        from db.engine import get_sessionmaker
        for i in range(n):
            async with get_sessionmaker()() as session:
                await claim_batch(session, f"worker-{i}", batch_size=10)
            # Reset back to pending between claims to simulate repeated
            # crash-and-reclaim cycles that each bump `attempts`.
            from sqlalchemy import text
            async with get_sessionmaker()() as session:
                await session.execute(
                    text("UPDATE anchor_outbox SET status='pending' WHERE step_id = ANY(:ids)"),
                    {"ids": [e["stepId"] for e in events]},
                )
                await session.commit()

    run(_claim_n_times(3))  # matches chain_settings.anchor_max_attempts

    async def _claim_and_backdate():
        from db.engine import get_sessionmaker
        from sqlalchemy import text
        async with get_sessionmaker()() as session:
            await claim_batch(session, "worker-final", batch_size=10)
        stale_claimed_at = int(time.time()) - 999
        async with get_sessionmaker()() as session:
            await session.execute(
                text("UPDATE anchor_outbox SET claimed_at = :t WHERE step_id = ANY(:ids)"),
                {"t": stale_claimed_at, "ids": [e["stepId"] for e in events]},
            )
            await session.commit()

    run(_claim_and_backdate())

    async def _reap():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            return await reap_stale_claims(session, claim_timeout_seconds=5, max_attempts=3)

    result = run(_reap())
    assert result["reset"] == []
    assert len(result["dead_lettered"]) == 1


@requires_anvil
def test_handle_submit_failure_requeues_steps_for_rebatching(chain_settings):
    """Forces a real first-attempt on-chain revert — submitting through a
    signer that was never granted ANCHOR_ROLE, so AgentAuditLogV2's
    onlyRole(ANCHOR_ROLE) modifier reverts the transaction — and confirms
    handle_submit_failure detaches the failed batch's steps
    (anchor_batch_id/batch_id -> NULL, outbox -> pending, since attempts=1
    is under max_attempts=3) rather than leaving them stranded pointing at
    a batch that never confirmed."""
    from eth_account import Account

    from blockchain.signer import LocalKeySigner

    run_id = _unique_run_id("run_submit_fail_test")
    _seed_run_with_steps(run_id, 2)

    contract = chain_module.get_audit_log_contract()
    w3 = chain_module.get_w3()
    admin_signer = chain_module.get_signer()  # account #0 — funded, has ANCHOR_ROLE

    # A fresh account, funded (so it gets *past* send and reverts on-chain
    # instead of failing pre-send with "insufficient funds") but never
    # granted ANCHOR_ROLE — isolates the receipt.status != 1 revert path
    # from the pre-send failure path exercised elsewhere.
    fresh_account = Account.create()
    fund_tx = {
        "from": admin_signer.address,
        "to": fresh_account.address,
        "value": w3.to_wei(1, "ether"),
        "nonce": w3.eth.get_transaction_count(admin_signer.address, "pending"),
        "gas": 21_000,
        "gasPrice": w3.eth.gas_price,
    }
    signed_fund_tx = admin_signer.sign_transaction(fund_tx)
    fund_tx_hash = w3.eth.send_raw_transaction(signed_fund_tx.raw_transaction)
    w3.eth.wait_for_transaction_receipt(fund_tx_hash, timeout=30)

    unprivileged_signer = LocalKeySigner(fresh_account.key, w3)

    async def _claim_and_batch():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-a", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        return batches[0]

    batch = run(_claim_and_batch())

    async def _submit_and_handle_failure():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            try:
                await submit_batch(session, batch, contract, unprivileged_signer, w3, confirm_timeout=30)
                assert False, "expected SubmitError from an ANCHOR_ROLE revert"
            except SubmitError as e:
                await handle_submit_failure(session, batch, e, max_attempts=3)

    run(_submit_and_handle_failure())

    async def _fetch_state():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import AnchorBatch, AnchorOutbox, Step
        async with get_sessionmaker()() as session:
            steps = (await session.execute(select(Step).where(Step.run_id == run_id))).scalars().all()
            outboxes = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.step_id.in_([s.id for s in steps]))
            )).scalars().all()
            failed_batch = await session.get(AnchorBatch, batch["batch_id"])
            return steps, outboxes, failed_batch

    steps, outboxes, failed_batch = run(_fetch_state())
    assert failed_batch.status == "failed"
    assert all(s.anchor_batch_id is None for s in steps)
    assert all(o.batch_id is None for o in outboxes)
    assert all(o.status == "pending" for o in outboxes)
    assert all(o.attempts == 1 for o in outboxes)

    # And the requeued steps must actually be reclaimable and anchorable
    # by a subsequent, privileged run — proving detachment didn't leave
    # them in some in-between state a real worker couldn't recover from.
    anchored = run(run_once("worker-retry", chain_settings))
    assert anchored == 2
