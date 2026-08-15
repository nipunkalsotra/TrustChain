"""
anchor_worker/reaper.py — recovers outbox rows orphaned by a worker crash.

FOR UPDATE SKIP LOCKED (claim.py) guarantees two live workers never claim
the same row, but it does nothing for a worker that claims a row and then
dies before submit.py ever runs — that row stays status='claimed' forever,
since the Postgres row lock released the moment the claiming transaction
committed. This is what the chaos test exercises: kill the worker
mid-batch, then prove the *next* run recovers those steps instead of
losing them silently.

Rows past `claim_timeout_seconds` with attempts still under the cap go
back to 'pending' for reclaiming; rows that have exhausted their attempts
are dead-lettered instead of retried forever.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import observability


async def reap_stale_claims(session: AsyncSession, claim_timeout_seconds: int, max_attempts: int) -> dict:
    now = int(datetime.now(timezone.utc).timestamp())
    cutoff = now - claim_timeout_seconds

    dead = await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'dead_letter', last_error = 'exceeded max_attempts after stale claim'
            WHERE status = 'claimed' AND claimed_at <= :cutoff AND attempts >= :max_attempts
            RETURNING id
        """),
        {"cutoff": cutoff, "max_attempts": max_attempts},
    )
    dead_ids = [row.id for row in dead]
    if dead_ids:
        observability.ANCHOR_OUTBOX_DEAD_LETTERED_TOTAL.labels(source="reaper").inc(len(dead_ids))

    reset = await session.execute(
        text("""
            UPDATE anchor_outbox
            SET status = 'pending', claimed_by = NULL, claimed_at = NULL, next_attempt_at = :now
            WHERE status = 'claimed' AND claimed_at <= :cutoff AND attempts < :max_attempts
            RETURNING id
        """),
        {"cutoff": cutoff, "max_attempts": max_attempts, "now": now},
    )
    reset_ids = [row.id for row in reset]
    if reset_ids:
        observability.ANCHOR_REAPER_RESET_TOTAL.inc(len(reset_ids))

    await session.commit()
    return {"reset": reset_ids, "dead_lettered": dead_ids}
