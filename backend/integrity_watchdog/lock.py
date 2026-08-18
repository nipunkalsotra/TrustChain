"""
integrity_watchdog/lock.py — sole-active-sweeper Postgres advisory lock.

Same shape and same reasoning as anchor_worker/nonce_lock.py: SKIP LOCKED
(used inside the detectors' own claim-style queries where relevant) makes
an accidental second instance non-catastrophic at the row level, but two
watchdog instances both actively sweeping would still duplicate work and
race each other's watchdog_cursor updates. This lock enforces "exactly
one active sweeper" the same way that module enforces "exactly one nonce
authority" — a distinct, fixed key (config.watchdog_advisory_lock_key)
so it can never collide with the anchor worker's lock.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from config import get_settings
from db.engine import get_engine
from logging_config import get_logger

logger = get_logger(__name__)


class WatchdogLock:
    def __init__(self):
        self._connection: AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        key = get_settings().watchdog_advisory_lock_key
        connection = await get_engine().connect()
        result = await connection.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        acquired = result.scalar_one()
        if acquired:
            self._connection = connection
            return True
        await connection.close()
        return False

    async def release(self) -> None:
        if self._connection is None:
            return
        key = get_settings().watchdog_advisory_lock_key
        try:
            await self._connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        finally:
            await self._connection.close()
            self._connection = None
