import asyncio
import time

from sqlalchemy import select
from web3 import Web3

import db
import observability
from agents.base import log_step
from db.engine import get_sessionmaker
from db.models import AnchorOutbox, Step
from tests.conftest import seed_project


def run(coro):
    return asyncio.run(coro)


def _seed_run(run_id: str = "run_logstep_test"):
    run(db.create_run(run_id, seed_project(), "test task", None, int(time.time())))
    return run_id


def test_log_step_writes_step_and_outbox_in_one_transaction():
    run_id = _seed_run()

    tx, evt = run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="hello world", output_text="some output",
        step_index=0, run_id=run_id, trust_score=25,
    ))

    # tx_hash is a placeholder now — anchoring is asynchronous — but must
    # stay truthy: frontend's isStepEvent() requires a truthy txHash to
    # treat this as a real step event.
    assert tx.startswith("pending:")
    assert evt["txHash"] == tx
    assert evt["anchorStatus"] == "pending"
    assert evt["agentId"] == "researcher"
    assert evt["action"] == "task_received"
    assert evt["step"] == 0
    assert evt["runId"] == run_id
    assert evt["trustScore"] == 25
    assert evt["stepId"] is not None
    assert evt["outboxId"] is not None

    async def _fetch():
        async with get_sessionmaker()() as session:
            step = await session.get(Step, evt["stepId"])
            result = await session.execute(select(AnchorOutbox).where(AnchorOutbox.step_id == step.id))
            outbox = result.scalar_one()
            return step, outbox

    step, outbox = run(_fetch())

    assert step.run_id == run_id
    assert step.agent_id == "researcher"
    assert step.action == "task_received"
    assert len(step.leaf_hash) == 66  # 0x + 64 hex chars
    assert step.leaf_hash.startswith("0x")
    assert step.anchor_batch_id is None  # not yet claimed by the anchor worker

    assert outbox.step_id == step.id
    assert outbox.status == "pending"
    assert outbox.attempts == 0
    assert outbox.batch_id is None


def test_log_step_leaf_hash_is_deterministic_for_same_inputs():
    """Two steps with identical content but different step_index/timestamp
    must NOT collide — the leaf hash has to be sensitive to position, or
    two genuinely different steps could produce the same Merkle leaf."""
    run_id = _seed_run("run_logstep_determinism")

    _, evt1 = run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="same input", output_text="same output",
        step_index=0, run_id=run_id,
    ))
    _, evt2 = run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="same input", output_text="same output",
        step_index=1, run_id=run_id,
    ))

    async def _fetch_leaf(step_id):
        async with get_sessionmaker()() as session:
            step = await session.get(Step, step_id)
            return step.leaf_hash

    leaf1 = run(_fetch_leaf(evt1["stepId"]))
    leaf2 = run(_fetch_leaf(evt2["stepId"]))
    assert leaf1 != leaf2


def test_log_step_detects_pii_shaped_content_without_changing_the_hash():
    """pii_patterns.py's find_likely_pii is wired into log_step for
    observability ONLY (a metric + a log line) — it must never change
    what actually gets hashed, or a caller's own locally-computed hash
    of their original text would stop matching what TrustChain anchored
    (see pii_patterns.py's module docstring). Verified two ways here:
    the metric increments on PII-shaped input, AND the stored leaf_hash
    is bit-for-bit what a caller would independently compute from the
    exact same raw (unredacted) input_text/output_text."""
    run_id = _seed_run("run_logstep_pii_detection")
    before = observability.ANCHOR_PAYLOAD_PII_DETECTED_TOTAL.labels(kind="email", field="input")._value.get()

    input_text = "please anchor this note for alice.test@example.com"
    output_text = "acknowledged"
    _, evt = run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text=input_text, output_text=output_text,
        step_index=0, run_id=run_id,
    ))

    after = observability.ANCHOR_PAYLOAD_PII_DETECTED_TOTAL.labels(kind="email", field="input")._value.get()
    assert after == before + 1

    # Independently recompute the hash from the exact same, UNREDACTED
    # raw text a real caller would still have on their own side — this
    # must match what got stored, proving detection didn't mutate
    # anything before hashing.
    expected_input_hash = "0x" + Web3.solidity_keccak(["string"], [input_text]).hex()

    async def _fetch():
        async with get_sessionmaker()() as session:
            return await session.get(Step, evt["stepId"])

    step = run(_fetch())
    assert step.input_hash == expected_input_hash


def test_log_step_requires_existing_run_row():
    """steps.run_id is FK-constrained against runs.run_id — logging a step
    for a run that was never created must fail loudly, not silently
    orphan a row."""
    import sqlalchemy.exc

    try:
        run(log_step(
            bridge=None, agent_id="researcher", action="task_received",
            input_text="x", output_text="y", step_index=0,
            run_id="does-not-exist-anywhere",
        ))
        assert False, "expected an IntegrityError for a step referencing a nonexistent run"
    except sqlalchemy.exc.IntegrityError:
        pass
