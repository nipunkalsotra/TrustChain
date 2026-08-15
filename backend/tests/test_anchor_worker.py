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
import observability
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
    _seed_run_with_steps(run_id, 5)

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

    # Real gas-spend attribution (db/models.py's AnchorBatch.gas_used/
    # gas_price_wei) — read straight off the real confirmation receipt,
    # not estimated. Cross-checked against the real receipt fetched
    # independently here, not just "is non-null".
    async def _fetch_batch_row():
        from db.engine import get_sessionmaker
        from db.models import AnchorBatch
        async with get_sessionmaker()() as session:
            return await session.get(AnchorBatch, batch["batch_id"])

    batch_row = run(_fetch_batch_row())
    w3 = chain_module.get_w3()
    real_receipt = w3.eth.get_transaction_receipt(result["tx_hash"])
    assert batch_row.gas_used == real_receipt.gasUsed
    assert batch_row.gas_used > 0
    assert batch_row.gas_price_wei == real_receipt.effectiveGasPrice
    assert batch_row.gas_price_wei > 0

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

    reset_before = observability.ANCHOR_REAPER_RESET_TOTAL._value.get()
    result = run(_reap())
    assert len(result["reset"]) == 2
    assert result["dead_lettered"] == []
    assert observability.ANCHOR_REAPER_RESET_TOTAL._value.get() == reset_before + 2

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

    before = observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="reaper")._value.get()
    result = run(_reap())
    assert result["reset"] == []
    assert len(result["dead_lettered"]) == 1
    after = observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="reaper")._value.get()
    assert after == before + 1


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
                await handle_submit_failure(
                    session, batch, e, max_attempts=3,
                    backoff_base_seconds=chain_settings.anchor_backoff_base_seconds,
                    backoff_max_seconds=chain_settings.anchor_backoff_max_seconds,
                )

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


@requires_anvil
def test_handle_submit_failure_dead_letters_when_attempts_exhausted(chain_settings):
    """Same real on-chain-revert setup as the requeue test above, but with
    max_attempts=1 — claim_batch already bumped attempts to 1 at claim
    time (see claim.py), so attempts >= max_attempts is true on this
    FIRST failure, forcing handle_submit_failure's dead-letter branch
    instead of the requeue one. Proves observability.
    ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL{source="submit_failure"} — the
    counter AnchorOutboxStepsDeadLettered alerts on — actually increments
    on this path, not just the reaper's."""
    from eth_account import Account

    from blockchain.signer import LocalKeySigner

    run_id = _unique_run_id("run_submit_fail_dead_letter_test")
    _seed_run_with_steps(run_id, 1)

    contract = chain_module.get_audit_log_contract()
    w3 = chain_module.get_w3()
    admin_signer = chain_module.get_signer()

    fresh_account = Account.create()
    fund_tx = {
        "from": admin_signer.address, "to": fresh_account.address,
        "value": w3.to_wei(1, "ether"),
        "nonce": w3.eth.get_transaction_count(admin_signer.address, "pending"),
        "gas": 21_000, "gasPrice": w3.eth.gas_price,
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

    before = observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="submit_failure")._value.get()

    async def _submit_and_handle_failure():
        from db.engine import get_sessionmaker
        async with get_sessionmaker()() as session:
            try:
                await submit_batch(session, batch, contract, unprivileged_signer, w3, confirm_timeout=30)
                assert False, "expected SubmitError from an ANCHOR_ROLE revert"
            except SubmitError as e:
                await handle_submit_failure(
                    session, batch, e, max_attempts=1,
                    backoff_base_seconds=chain_settings.anchor_backoff_base_seconds,
                    backoff_max_seconds=chain_settings.anchor_backoff_max_seconds,
                )

    run(_submit_and_handle_failure())

    after = observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="submit_failure")._value.get()
    assert after == before + 1

    async def _fetch_state():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import AnchorOutbox, Step
        async with get_sessionmaker()() as session:
            steps = (await session.execute(select(Step).where(Step.run_id == run_id))).scalars().all()
            outboxes = (await session.execute(
                select(AnchorOutbox).where(AnchorOutbox.step_id.in_([s.id for s in steps]))
            )).scalars().all()
            return outboxes

    outboxes = run(_fetch_state())
    assert all(o.status == "dead_letter" for o in outboxes)


@requires_anvil
def test_run_once_samples_real_wallet_balance(chain_settings):
    """run_once() must sample the signer's REAL Anvil-reported balance
    into observability.ANCHOR_WALLET_BALANCE_WEI every iteration — this
    is what AnchorWalletBalanceLow (docker/prometheus/alerts.yml) alerts
    on, and it can only ever be wrong if it drifts from the chain's own
    answer, so the check compares directly against eth_getBalance."""
    import observability

    w3 = chain_module.get_w3()
    signer = chain_module.get_signer()

    run(run_once("worker-balance-sample", chain_settings))

    real_balance = w3.eth.get_balance(signer.address)
    sampled = observability.ANCHOR_WALLET_BALANCE_WEI._value.get()
    # Prometheus Gauges are float64 internally (the whole exposition
    # format is), so a wei-scale integer (~10**21 for a funded Anvil
    # account) loses precision the instant it's stored — expected, not a
    # bug in the sampling itself. A relative tolerance is the honest check.
    assert sampled == pytest.approx(real_balance, rel=1e-9)
    assert sampled > 0  # Anvil's default account #0 starts funded


@requires_anvil
def test_exponential_backoff_delays_retry_and_grows_with_attempts(chain_settings):
    """Real proof that handle_submit_failure's backoff isn't just a
    config knob that's never wired up: attempt 1's failure sets
    next_attempt_at ~backoff_base seconds out (not `now`, which claim_batch's
    own `next_attempt_at <= now` filter would otherwise reclaim
    immediately); a second failure (attempts bumped to 2, simulating time
    having passed by writing next_attempt_at back to "claimable now"
    directly — the same technique test_reaper_recovers_claims_orphaned_by_
    a_crashed_worker uses to simulate elapsed time without a real sleep)
    produces a proportionally LARGER delay, proving the exponent term
    actually depends on the row's own `attempts`, not just attempt 1."""
    from datetime import datetime, timezone

    from eth_account import Account
    from sqlalchemy import text

    from blockchain.signer import LocalKeySigner
    from db.engine import get_sessionmaker

    backoff_base = 10.0
    backoff_max = 1000.0

    run_id = _unique_run_id("run_backoff_test")
    _seed_run_with_steps(run_id, 1)

    contract = chain_module.get_audit_log_contract()
    w3 = chain_module.get_w3()
    admin_signer = chain_module.get_signer()

    fresh_account = Account.create()
    fund_tx = {
        "from": admin_signer.address, "to": fresh_account.address,
        "value": w3.to_wei(1, "ether"),
        "nonce": w3.eth.get_transaction_count(admin_signer.address, "pending"),
        "gas": 21_000, "gasPrice": w3.eth.gas_price,
    }
    signed = admin_signer.sign_transaction(fund_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    unprivileged_signer = LocalKeySigner(fresh_account.key, w3)

    # Claims, submits (a real on-chain revert via the unprivileged
    # signer), and hands the failure to handle_submit_failure — called
    # twice below, once per attempt, to observe the SAME row's delay grow.
    async def _first_failure():
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-backoff", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        batch = batches[0]

        now_before = int(datetime.now(timezone.utc).timestamp())
        async with get_sessionmaker()() as session:
            try:
                await submit_batch(session, batch, contract, unprivileged_signer, w3, confirm_timeout=30)
                assert False, "expected a real ANCHOR_ROLE revert"
            except SubmitError as e:
                await handle_submit_failure(
                    session, batch, e, max_attempts=5,
                    backoff_base_seconds=backoff_base, backoff_max_seconds=backoff_max,
                )

        async with get_sessionmaker()() as session:
            row = (await session.execute(
                text("""
                    SELECT o.id, o.next_attempt_at, o.attempts
                    FROM anchor_outbox o JOIN steps s ON s.id = o.step_id
                    WHERE s.run_id = :run_id
                """),
                {"run_id": run_id},
            )).first()
        return now_before, row

    now_before_1, row_1 = run(_first_failure())
    assert row_1.attempts == 1
    delay_1 = row_1.next_attempt_at - now_before_1
    # base * 2^0 = base, allow a couple seconds of real wall-clock slack
    # for the on-chain revert round-trip between now_before and the UPDATE.
    assert backoff_base - 2 <= delay_1 <= backoff_base + 5, f"attempt 1 delay was {delay_1}s, expected ~{backoff_base}s"

    # Simulate the backoff having elapsed (real infra, but we don't want a
    # real 10s+ sleep in a test) by writing next_attempt_at back to now —
    # the same "simulate elapsed time via direct UPDATE" technique this
    # file already uses for reaper tests. attempts stays at 1 (real state
    # from the real first failure) so the second claim bumps it to 2.
    async def _make_reclaimable_now():
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE anchor_outbox SET next_attempt_at = 0 WHERE id = :id"), {"id": row_1.id})
            await session.commit()
    run(_make_reclaimable_now())

    now_before_2, row_2 = run(_first_failure())
    assert row_2.attempts == 2
    delay_2 = row_2.next_attempt_at - now_before_2
    # base * 2^1 = base*2 — genuinely larger than delay_1, not coincidence.
    assert backoff_base * 2 - 2 <= delay_2 <= backoff_base * 2 + 5, f"attempt 2 delay was {delay_2}s, expected ~{backoff_base*2}s"
    assert delay_2 > delay_1


@requires_anvil
def test_max_batch_age_flush_trigger(chain_settings):
    """run_once() must hold a trickle of pending steps (well under
    anchor_max_batch_size) unflushed until the oldest one has been
    waiting at least anchor_max_batch_age_seconds — then flush it for
    real on the next poll. Uses a real Settings override (a big batch
    size so the count threshold can't accidentally fire, a real age
    threshold) and the same "simulate elapsed time via direct SQL
    UPDATE" technique the backoff test above uses, rather than a real
    sleep."""
    from sqlalchemy import text

    from db.engine import get_sessionmaker

    aged_settings = chain_settings.model_copy(
        update={"anchor_max_batch_size": 1000, "anchor_max_batch_age_seconds": 3600}
    )

    run_id = _unique_run_id("run_batch_age_test")
    _seed_run_with_steps(run_id, 2)

    # First poll: 2 pending, batch size is 1000 (no count-trigger) and the
    # rows were just created (age ~0s, nowhere near the 3600s threshold)
    # -> must defer, not claim/anchor anything.
    anchored_before = run(run_once("worker-batch-age", aged_settings))
    assert anchored_before == 0

    async def _status_for_run():
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                text("""
                    SELECT o.status FROM anchor_outbox o JOIN steps s ON s.id = o.step_id
                    WHERE s.run_id = :run_id
                """),
                {"run_id": run_id},
            )).all()
        return [r.status for r in rows]

    assert run(_status_for_run()) == ["pending", "pending"]

    # Simulate the batch having aged past the threshold by backdating
    # created_at, same "direct UPDATE instead of a real sleep" technique
    # used elsewhere in this file.
    async def _age_rows():
        async with get_sessionmaker()() as session:
            await session.execute(
                text("""
                    UPDATE anchor_outbox SET created_at = created_at - 4000
                    WHERE step_id IN (SELECT id FROM steps WHERE run_id = :run_id)
                """),
                {"run_id": run_id},
            )
            await session.commit()
    run(_age_rows())

    # Second poll: still only 2 pending (no count-trigger), but oldest_age
    # (~4000s) now exceeds anchor_max_batch_age_seconds (3600s) -> must
    # flush for real, anchoring both steps on-chain.
    anchored_after = run(run_once("worker-batch-age", aged_settings))
    assert anchored_after == 2
    assert run(_status_for_run()) == ["anchored", "anchored"]


@requires_anvil
def test_submit_batch_replaces_by_fee_when_first_attempt_never_confirms(chain_settings):
    """A tx that never confirms within confirm_timeout must be replaced —
    same nonce, bumped fee, a genuinely different tx_hash — not abandoned
    for a fresh nonce (which would just queue behind the stuck one). Real
    Anvil has no mempool congestion to force a slow confirmation on
    demand, so this pauses Anvil's automine (evm_setAutomine) so the first
    send genuinely cannot be mined, lets confirm_timeout genuinely elapse,
    then mines a block only once the (distinct, bumped-fee) replacement
    tx is already in flight — proving Anvil's mempool accepted the
    replacement at the same nonce, and it (not the original) is what
    actually got confirmed."""
    w3 = chain_module.get_w3()
    contract = chain_module.get_audit_log_contract()
    signer = chain_module.get_signer()

    run_id = _unique_run_id("run_rbf_test")
    _seed_run_with_steps(run_id, 1)

    from db.engine import get_sessionmaker

    async def _claim_and_batch():
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-rbf", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        return batches[0]

    batch = run(_claim_and_batch())

    async def _submit_with_delayed_mine():
        async def _mine_after_delay():
            # Long enough that the first attempt has genuinely timed out
            # (confirm_timeout=3) and the replacement has genuinely been
            # sent, short enough to land well inside the second attempt's
            # own confirm_timeout window.
            await asyncio.sleep(3.5)
            w3.provider.make_request("evm_mine", [])

        async with get_sessionmaker()() as session:
            submit_task = submit_batch(
                session, batch, contract, signer, w3,
                confirm_timeout=3, rbf_max_attempts=3, rbf_fee_bump_fraction=0.5,
            )
            result, _ = await asyncio.gather(submit_task, _mine_after_delay())
            return result

    w3.provider.make_request("evm_setAutomine", [False])
    try:
        before = observability.ANCHOR_BATCHES_REPLACED_TOTAL._value.get()
        result = run(_submit_with_delayed_mine())
    finally:
        # Must always restore automine — this Anvil instance is shared
        # with the rest of the suite (and the live anchor-worker/indexer
        # containers), so leaving it paused would silently freeze every
        # later on-chain confirmation, not just this test's.
        w3.provider.make_request("evm_setAutomine", [True])

    assert result["status"] == "confirmed"
    after = observability.ANCHOR_BATCHES_REPLACED_TOTAL._value.get()
    assert after == before + 1, "expected exactly one replace-by-fee resubmission"
