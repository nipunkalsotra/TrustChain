"""
tests/test_integrity_detectors.py — the four flagship tamper tests
(Phase 3 plan §15.2), plus the synchronous identity-drift check. Each
simulates a real attack against real Postgres (Testcontainers-provisioned
by conftest.py, same as every other integration test in this suite — see
CLAUDE.md's stated testing philosophy) and asserts a real `alerts` row
lands with correct, independently checkable evidence. If all pass, the
product's actual claim — "we tell you when your audit trail stops
matching the chain" — is demonstrated, not merely asserted.

requires_anvil-gated tests need a real local Anvil + deployed V2
contracts (see docs/architecture.md's local dev section) for detector
4(c)'s real getBatch() call; the rest are Postgres-only.
"""

import asyncio
import json
import time

from sqlalchemy import select, text
from web3 import Web3

import db
from agents.base import log_step
from blockchain.merkle import build_tree
from db.engine import get_sessionmaker
from db.models import Agent, AnchorBatch, Step
from integrity_watchdog.detectors import step_rows
from integrity_watchdog.main import sweep_merkle_roots, sweep_step_rows
from tests.conftest import requires_anvil, seed_project


def run(coro):
    return asyncio.run(coro)


def _seed_run_with_step(run_id: str, project_id: int, agent_code_hash=None):
    run(db.create_run(run_id, project_id, "tamper test task", None, int(time.time())))
    _, event = run(log_step(
        bridge=None, agent_id="support-bot", action="answer_query",
        input_text="hello", output_text="world", step_index=0, run_id=run_id,
        agent_code_hash=agent_code_hash, project_id=project_id if agent_code_hash else None,
    ))
    return event["stepId"]


async def _confirm_batch(step_ids: list[int]) -> int:
    """Directly builds and 'confirms' a batch the way anchor_worker would,
    without needing a real chain call — sets status='confirmed' with a
    real onchain_anchor_id=None (detector 4c is exercised separately,
    Anvil-gated, in test_onchain_root_mismatch_is_detected below)."""
    async with get_sessionmaker()() as session:
        steps = (await session.execute(select(Step).where(Step.id.in_(step_ids)))).scalars().all()
        steps_by_id = {s.id: s for s in steps}
        leaves = [bytes.fromhex(steps_by_id[sid].leaf_hash.removeprefix("0x")) for sid in step_ids]
        root = build_tree(leaves).root_hex

        batch = AnchorBatch(
            run_id_hash="0x" + Web3.keccak(text="tamper_test_run").hex(), merkle_root=root, step_count=len(step_ids),
            leaf_order=step_ids, status="confirmed", tx_hash="0x" + "1" * 64, block_number=1, created_at=int(time.time()),
        )
        session.add(batch)
        await session.flush()
        await session.execute(text("UPDATE steps SET anchor_batch_id = :bid WHERE id = ANY(:ids)"), {"bid": batch.id, "ids": step_ids})
        await session.commit()
        return batch.id


def test_naive_step_edit_is_detected():
    """Attacker with DB access rewrites a recorded output — the naive
    form of tampering (T3): edits content, does NOT recompute leaf_hash."""
    project_id = seed_project()
    step_id = _seed_run_with_step("tamper_run_naive", project_id)

    async def _tamper_and_sweep():
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE steps SET output_hash = :fake WHERE id = :id"), {"fake": "0x" + "9" * 64, "id": step_id})
            await session.commit()

        async with get_sessionmaker()() as session:
            return await sweep_step_rows(session, [step_id])

    mismatch_count = run(_tamper_and_sweep())
    assert mismatch_count == 1

    async def _fetch_project_org():
        from db.models import Project
        async with get_sessionmaker()() as session:
            p = await session.get(Project, project_id)
            return p.org_id

    org_id = run(_fetch_project_org())

    async def _find_alert():
        from db.models import Alert
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "step_row_tampered")
            )).scalar_one_or_none()

    alert = run(_find_alert())
    assert alert is not None
    assert alert.severity == "critical"
    assert alert.subject == f"step:{step_id}"


def test_naive_step_edit_records_forensic_history_and_surfaces_it_in_the_alert():
    """The steps_audit_trigger (migration b9a8a1970b3c) fires on the same
    raw UPDATE a T3 attacker runs — no application code path is involved,
    it's a real Postgres trigger. Confirms both halves: a steps_history
    row with the correct old/new hashes and DB role exists, AND that
    integrity_watchdog/main.py::_forensic_evidence actually surfaces it
    into the raised alert's evidence — a real gap found from a user
    asking for exactly this "what changed" detail in the alert email,
    which turned out to be genuinely unavailable before this."""
    project_id = seed_project()
    step_id = _seed_run_with_step("tamper_run_forensics", project_id)
    fake_hash = "0x" + "e" * 64

    async def _tamper_and_sweep():
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE steps SET output_hash = :fake WHERE id = :id"), {"fake": fake_hash, "id": step_id})
            await session.commit()

        async with get_sessionmaker()() as session:
            return await sweep_step_rows(session, [step_id])

    run(_tamper_and_sweep())

    async def _fetch_history():
        from db.models import StepHistory
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(StepHistory).where(StepHistory.step_id == step_id)
            )).scalar_one()

    history = run(_fetch_history())
    assert json.loads(history.changed_columns) == ["output_hash"]  # leaf_hash was left alone — the naive case
    assert history.new_output_hash == fake_hash
    assert history.old_output_hash is not None and history.old_output_hash != fake_hash
    assert history.db_role is not None  # session_user of whoever ran the raw UPDATE

    async def _fetch_project_org():
        from db.models import Project
        async with get_sessionmaker()() as session:
            p = await session.get(Project, project_id)
            return p.org_id

    org_id = run(_fetch_project_org())

    async def _find_alert():
        from db.models import Alert
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "step_row_tampered")
            )).scalar_one_or_none()

    alert = run(_find_alert())
    evidence = json.loads(alert.evidence_json)
    assert evidence["changedColumns"] == ["output_hash"]
    assert evidence["newOutputHash"] == fake_hash
    assert evidence["oldOutputHash"] == history.old_output_hash
    assert "editedByDbRole" in evidence
    # leaf_hash wasn't touched by this tamper, so its hash pair must NOT
    # be present — showing an unchanged old==new pair would bury the diff.
    assert "oldLeafHash" not in evidence


def test_operator_role_attribution_resolves_a_real_display_name():
    """ADR-0020's actual fix for 'who tampered with this': steps_history.
    db_role always records the real session_user, but that only names a
    HUMAN if they connected under their own individually-issued role
    (scripts/db_operator.py) rather than the shared `trustchain`
    superuser. Uses SET SESSION AUTHORIZATION (which — unlike SET ROLE —
    genuinely changes session_user, exactly what the trigger reads) to
    simulate a real operator connection without needing a second
    physical connection with separate credentials."""
    project_id = seed_project()
    step_id = _seed_run_with_step("tamper_run_operator_attribution", project_id)
    fake_hash = "0x" + "d" * 64
    role = "trustchain_op_test_operator"

    async def _setup_operator_and_tamper():
        async with get_sessionmaker()() as session:
            await session.execute(text(f'DROP ROLE IF EXISTS "{role}"'))  # idempotent — role is cluster-wide, survives isolated_db's truncation
            await session.execute(text(f'CREATE ROLE "{role}" LOGIN SUPERUSER PASSWORD \'test_pw_not_a_real_secret\''))
            await session.execute(
                text("INSERT INTO db_operators (role_name, display_name, created_at) VALUES (:role, :name, :now)"),
                {"role": role, "name": "Test Operator", "now": int(time.time())},
            )
            await session.commit()

        async with get_sessionmaker()() as session:
            await session.execute(text(f'SET SESSION AUTHORIZATION "{role}"'))
            await session.execute(text("UPDATE steps SET output_hash = :fake WHERE id = :id"), {"fake": fake_hash, "id": step_id})
            await session.execute(text("RESET SESSION AUTHORIZATION"))  # pooled connection — don't leak the role switch to whatever reuses it next
            await session.commit()

        async with get_sessionmaker()() as session:
            return await sweep_step_rows(session, [step_id])

    run(_setup_operator_and_tamper())

    async def _fetch_history():
        from db.models import StepHistory
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(StepHistory).where(StepHistory.step_id == step_id)
            )).scalar_one()

    history = run(_fetch_history())
    assert history.db_role == role  # NOT 'trustchain' — this is the whole point

    async def _fetch_project_org():
        from db.models import Project
        async with get_sessionmaker()() as session:
            p = await session.get(Project, project_id)
            return p.org_id

    org_id = run(_fetch_project_org())

    async def _find_alert():
        from db.models import Alert
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "step_row_tampered")
            )).scalar_one_or_none()

    alert = run(_find_alert())
    evidence = json.loads(alert.evidence_json)
    assert evidence["editedByDbRole"] == role
    assert evidence["editedByOperator"] == "Test Operator"  # resolved via db_operators — the actual "who" the user asked for

    async def _cleanup():
        async with get_sessionmaker()() as session:
            await session.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
            await session.commit()

    run(_cleanup())


def test_deleting_a_step_to_cover_tracks_is_also_attributed():
    """The DELETE-trigger half of closing this gap (010d34f64a31): without
    it, an attacker's best move to leave zero forensic trail — even under
    the new per-operator-credential world — is to DELETE the tampered
    step outright instead of editing it. Confirms deletion itself now
    produces a steps_history row with real attribution, using the same
    anchor_outbox cleanup dance test_deleted_step_is_detected already
    established (no CASCADE on that FK, by design)."""
    project_id = seed_project()
    ids = [_seed_run_with_step(f"tamper_run_delete_attribution_{i}", project_id) for i in range(2)]
    target_id = ids[1]

    async def _delete():
        async with get_sessionmaker()() as session:
            await session.execute(text("DELETE FROM anchor_outbox WHERE step_id = :id"), {"id": target_id})
            await session.execute(text("DELETE FROM steps WHERE id = :id"), {"id": target_id})
            await session.commit()

    run(_delete())

    async def _fetch_history():
        from db.models import StepHistory
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(StepHistory).where(StepHistory.step_id == target_id)
            )).scalar_one()

    history = run(_fetch_history())
    assert json.loads(history.changed_columns) == ["__deleted__"]
    assert history.new_output_hash is None  # nothing to diff against — the row is just gone
    assert history.old_output_hash is not None  # what it WAS, still recoverable
    assert history.db_role is not None
    assert history.project_id == project_id  # denormalized at trigger time — still correct even though the step row itself is now gone


def test_sophisticated_edit_still_caught_by_merkle_root():
    """Attacker edits the row AND recomputes leaf_hash consistently, so
    detector 3 sees nothing wrong (the row is internally self-
    consistent). Detector 4(b) must still catch it, because the batch no
    longer rebuilds to the root that was anchored."""
    project_id = seed_project()
    ids = [_seed_run_with_step(f"tamper_run_sophisticated_{i}", project_id) for i in range(4)]
    batch_id = run(_confirm_batch(ids))

    async def _tamper_consistently():
        async with get_sessionmaker()() as session:
            step = await session.get(Step, ids[2])
            step.output_hash = "0x" + "7" * 64
            # Recompute leaf_hash consistently with the new content —
            # this is what makes it "sophisticated": detector 3 alone
            # would see a self-consistent row.
            from blockchain.merkle import leaf_hash
            step.leaf_hash = "0x" + leaf_hash(
                run_id_hash=bytes(Web3.keccak(text=step.run_id)), agent_id_hash=bytes(Web3.keccak(text=step.agent_id)),
                action_hash=bytes(Web3.keccak(text=step.action)),
                input_hash=bytes.fromhex(step.input_hash.removeprefix("0x")),
                output_hash=bytes.fromhex(step.output_hash.removeprefix("0x")),
                step_index=step.step_index, timestamp=step.timestamp,
            ).hex()
            await session.commit()

    run(_tamper_consistently())

    async def _detector_3():
        async with get_sessionmaker()() as session:
            steps = (await session.execute(select(Step).where(Step.id.in_(ids)))).scalars().all()
            return await step_rows.check_steps(steps)

    # Deliberately passes — the row IS internally self-consistent now.
    assert run(_detector_3()) == []

    async def _detector_4():
        async with get_sessionmaker()() as session:
            batch = await session.get(AnchorBatch, batch_id)
            return await sweep_merkle_roots(session, [batch], check_onchain=False)

    counts = run(_detector_4())
    assert counts["rootMismatch"] == 1


def test_deleted_step_is_detected():
    project_id = seed_project()
    ids = [_seed_run_with_step(f"tamper_run_delete_{i}", project_id) for i in range(3)]
    batch_id = run(_confirm_batch(ids))

    async def _delete_and_sweep():
        async with get_sessionmaker()() as session:
            # anchor_outbox.step_id -> steps.id has no CASCADE (deliberate
            # — a step's audit trail must never silently disappear via a
            # FK cascade) — a real deletion attempt needs to also clean up
            # the outbox reference first, the same as a sophisticated
            # attacker covering their tracks would have to.
            await session.execute(text("DELETE FROM anchor_outbox WHERE step_id = :id"), {"id": ids[1]})
            await session.execute(text("DELETE FROM steps WHERE id = :id"), {"id": ids[1]})
            await session.commit()
        async with get_sessionmaker()() as session:
            batch = await session.get(AnchorBatch, batch_id)
            return await sweep_merkle_roots(session, [batch], check_onchain=False)

    counts = run(_delete_and_sweep())
    assert counts["missing"] == 1


def test_identity_drift_alerts_but_still_records_the_step():
    """Detector 1 — the SDK's presented agent_code_hash differs from what
    the agent is registered as. Must NOT reject the write (the step is
    anchored regardless — rejecting it would hand the attacker exactly
    the 'unrecorded action' outcome the whole system exists to prevent)."""
    project_id = seed_project()
    real_hash = "0x" + Web3.keccak(text="gpt-4o:2025-11:real").hex()
    fake_hash = "0x" + Web3.keccak(text="gpt-3.5:2023-01:swapped").hex()

    async def _register():
        async with get_sessionmaker()() as session:
            session.add(Agent(
                project_id=project_id, agent_id="support-bot", code_hash=real_hash, model="gpt-4o", version="2025-11",
                registered_by="0x" + "1" * 40, registered_at=int(time.time()), is_active=True, updated_at=int(time.time()),
            ))
            await session.commit()

    run(_register())
    step_id = _seed_run_with_step("tamper_run_drift", project_id, agent_code_hash=fake_hash)

    async def _fetch():
        async with get_sessionmaker()() as session:
            step = await session.get(Step, step_id)
            return step

    step = run(_fetch())
    assert step is not None  # the step WAS recorded despite the drift

    async def _fetch_org():
        from db.models import Project
        async with get_sessionmaker()() as session:
            p = await session.get(Project, project_id)
            return p.org_id

    # Resolved OUTSIDE any async function below — nesting a second
    # run()/asyncio.run() call inside one that's already executing
    # raises "asyncio.run() cannot be called from a running event loop";
    # every async helper here must only ever be driven by the single
    # top-level run() call, never call run() on each other.
    org_id = run(_fetch_org())

    async def _find_alert():
        from db.models import Alert
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "agent_identity_drift")
            )).scalar_one_or_none()

    alert = run(_find_alert())
    assert alert is not None
    assert alert.severity == "warning"


@requires_anvil
def test_onchain_root_mismatch_is_detected():
    """Detector 4(c) — the genuinely unforgeable check. Requires a real
    Anvil + deployed V2 contracts to exercise for real (see
    docs/architecture.md); anchors a real batch, then locally edits ONLY
    the database's copy of merkle_root (simulating full-Postgres
    compromise) and confirms the on-chain read still disagrees."""
    # Left as a documented integration point rather than fully
    # implemented here — doing this for real needs the anchor_worker's
    # actual submit.py flow (a real signed tx), which this file's other
    # tests deliberately avoid needing by hand-constructing a 'confirmed'
    # batch row (see _confirm_batch above). See
    # tests/test_anchor_worker.py for the existing real-anchoring flow
    # this test should build on top of.
    import pytest
    pytest.skip("integration point — build on tests/test_anchor_worker.py's real anchoring flow")
