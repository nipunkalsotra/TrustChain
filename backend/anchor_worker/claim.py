"""
anchor_worker/claim.py — atomic outbox claiming.

Many anchor worker processes could run concurrently (horizontal scaling,
or just "the old process didn't die cleanly and a new one started"). Two
workers must never claim the same outbox row — `FOR UPDATE SKIP LOCKED`
makes concurrent claims mutually exclusive at the database level: a second
worker's claim query simply skips rows the first worker already has locked,
rather than blocking on them or double-claiming.

This alone does not handle a worker that claims rows and then crashes
before submitting — those rows stay `status='claimed'` forever unless
something resets them. That's reaper.py's job, not this module's.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def claim_batch(session: AsyncSession, worker_id: str, batch_size: int) -> list[dict]:
    """
    Atomically claims up to `batch_size` pending outbox rows (oldest
    first) and returns each joined with the step fields the batcher needs
    (run_id, leaf_hash, step_index) — read in the same transaction the
    claim happened in, so the caller never has to trust a separately-read
    Step row still matches what was just claimed.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    claim_result = await session.execute(
        text("""
            WITH claimed AS (
                SELECT id
                FROM anchor_outbox
                WHERE status = 'pending' AND next_attempt_at <= :now
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            UPDATE anchor_outbox
            SET status = 'claimed', claimed_by = :worker_id, claimed_at = :now,
                attempts = attempts + 1
            FROM claimed
            WHERE anchor_outbox.id = claimed.id
            RETURNING anchor_outbox.id, anchor_outbox.step_id
        """),
        {"now": now, "batch_size": batch_size, "worker_id": worker_id},
    )
    claimed_rows = [dict(row._mapping) for row in claim_result]
    if not claimed_rows:
        await session.commit()
        return []

    step_ids = [r["step_id"] for r in claimed_rows]
    step_result = await session.execute(
        text("""
            SELECT id, run_id, agent_id, action, input_hash, output_hash,
                   leaf_hash, step_index, timestamp
            FROM steps
            WHERE id = ANY(:step_ids)
        """),
        {"step_ids": step_ids},
    )
    steps_by_id = {row.id: dict(row._mapping) for row in step_result}
    await session.commit()

    return [{"outbox_id": r["id"], **steps_by_id[r["step_id"]]} for r in claimed_rows]
