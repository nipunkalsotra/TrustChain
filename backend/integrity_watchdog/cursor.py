"""
integrity_watchdog/cursor.py — the ROLLING tier's resumable sweep
position (Phase 3 §6.7).

One row per detector in `watchdog_cursor`: last_id is the highest
primary-key value swept so far. Each cycle scans forward from there, at
most `batch_size` rows, and WRAPS to 0 when it reaches the current max —
so a full pass over all history takes (total_rows / batch_size) cycles,
and per-cycle cost never depends on how much history exists, only on
batch_size. `wrapped_at` records when the most recent full pass
completed — GET /integrity/status's `full_sweep_age_seconds` is
`now - wrapped_at`.
"""

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_cursor(session: AsyncSession, detector: str) -> dict:
    result = await session.execute(
        text("SELECT last_id, wrapped_at, last_run_at, last_duration_ms FROM watchdog_cursor WHERE detector = :d"),
        {"d": detector},
    )
    row = result.first()
    if row is None:
        return {"lastId": 0, "wrappedAt": None, "lastRunAt": 0, "lastDurationMs": 0}
    return {"lastId": row.last_id, "wrappedAt": row.wrapped_at, "lastRunAt": row.last_run_at, "lastDurationMs": row.last_duration_ms}


async def advance_cursor(session: AsyncSession, detector: str, new_last_id: int, wrapped: bool, duration_ms: int) -> None:
    now = int(time.time())
    await session.execute(
        text("""
            INSERT INTO watchdog_cursor (detector, last_id, wrapped_at, last_run_at, last_duration_ms, updated_at)
            VALUES (:detector, :last_id, :wrapped_at, :now, :duration_ms, :now)
            ON CONFLICT (detector) DO UPDATE SET
                last_id = EXCLUDED.last_id,
                wrapped_at = CASE WHEN :wrapped THEN EXCLUDED.wrapped_at ELSE watchdog_cursor.wrapped_at END,
                last_run_at = EXCLUDED.last_run_at,
                last_duration_ms = EXCLUDED.last_duration_ms,
                updated_at = EXCLUDED.updated_at
        """),
        {
            "detector": detector, "last_id": new_last_id, "wrapped_at": now if wrapped else None,
            "now": now, "duration_ms": duration_ms, "wrapped": wrapped,
        },
    )
    await session.commit()
