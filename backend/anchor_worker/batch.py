"""
anchor_worker/batch.py — groups claimed steps into per-run Merkle batches.

One AnchorBatch is built per run_id within a claimed set, not one batch
across all of them: AgentAuditLogV2.getBatchesForRun() is how an auditor
looks up "everything anchored for run X", keyed on a single runIdHash.
Mixing steps from different runs into one tree would still be
cryptographically fine, but it would defeat that lookup — a batch
spanning two runs can't be attributed to both.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from blockchain.merkle import build_tree


async def build_batches(session: AsyncSession, claimed_steps: list[dict]) -> list[dict]:
    """
    `claimed_steps` is claim_batch()'s output. Returns one dict per created
    AnchorBatch: {batch_id, run_id, run_id_hash, root_hex, step_count,
    leaf_order, outbox_ids}, ready for submit_batch().
    """
    now = int(datetime.now(timezone.utc).timestamp())
    by_run: dict[str, list[dict]] = {}
    for step in claimed_steps:
        by_run.setdefault(step["run_id"], []).append(step)

    created = []
    for run_id, steps in by_run.items():
        steps.sort(key=lambda s: s["step_index"])
        leaves = [bytes.fromhex(s["leaf_hash"].removeprefix("0x")) for s in steps]
        tree = build_tree(leaves)
        run_id_hash = "0x" + bytes(Web3.keccak(text=run_id)).hex()
        leaf_order = [s["id"] for s in steps]
        outbox_ids = [s["outbox_id"] for s in steps]

        insert_result = await session.execute(
            text("""
                INSERT INTO anchor_batches
                    (run_id_hash, merkle_root, step_count, leaf_order, status, created_at)
                VALUES (:run_id_hash, :merkle_root, :step_count, :leaf_order, 'building', :now)
                RETURNING id
            """),
            {
                "run_id_hash": run_id_hash,
                "merkle_root": tree.root_hex,
                "step_count": len(steps),
                "leaf_order": json.dumps(leaf_order),
                "now": now,
            },
        )
        batch_id = insert_result.scalar_one()

        await session.execute(
            text("UPDATE steps SET anchor_batch_id = :batch_id WHERE id = ANY(:step_ids)"),
            {"batch_id": batch_id, "step_ids": leaf_order},
        )
        await session.execute(
            text("UPDATE anchor_outbox SET batch_id = :batch_id WHERE id = ANY(:outbox_ids)"),
            {"batch_id": batch_id, "outbox_ids": outbox_ids},
        )

        created.append({
            "batch_id": batch_id,
            "run_id": run_id,
            "run_id_hash": run_id_hash,
            "root_hex": tree.root_hex,
            "step_count": len(steps),
            "leaf_order": leaf_order,
            "outbox_ids": outbox_ids,
        })

    await session.commit()
    return created
