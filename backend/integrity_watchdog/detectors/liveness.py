"""
integrity_watchdog/detectors/liveness.py — Detector 5 (Phase 3 §6.6):
anchoring liveness. Not a tamper detector like 1-4 — an audit trail that
never reaches the chain is unverifiable, which is a failure of the
product's core promise even though nobody attacked anything.

Two conditions, both cheap (pure Postgres aggregate queries, no RPC):
  - anchor_outbox rows stuck at pending/claimed older than a threshold
    (warning, escalating to critical after 2h) — the anchor worker isn't
    keeping up, or is down.
  - any row in dead_letter — retries were exhausted; that step will never
    be anchored without operator intervention.
"""

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_stalled(session: AsyncSession, stall_threshold_seconds: int) -> dict:
    """Returns {"pendingCount", "oldestPendingAgeSeconds", "oldestStepId"}."""
    now = int(time.time())
    result = await session.execute(
        text("""
            SELECT count(*), min(created_at), (array_agg(step_id ORDER BY created_at))[1]
            FROM anchor_outbox WHERE status IN ('pending', 'claimed')
        """)
    )
    row = result.first()
    count = row[0] or 0
    oldest_created_at = row[1]
    oldest_step_id = row[2]
    age = (now - oldest_created_at) if oldest_created_at is not None else 0
    return {
        "pendingCount": count, "oldestPendingAgeSeconds": age, "oldestStepId": oldest_step_id,
        "stalled": count > 0 and age >= stall_threshold_seconds,
    }


async def check_dead_lettered(session: AsyncSession) -> dict:
    result = await session.execute(
        text("SELECT array_agg(id), array_agg(step_id) FROM anchor_outbox WHERE status = 'dead_letter'")
    )
    row = result.first()
    outbox_ids = row[0] or []
    step_ids = row[1] or []
    return {"outboxIds": outbox_ids, "stepIds": step_ids, "count": len(outbox_ids)}
