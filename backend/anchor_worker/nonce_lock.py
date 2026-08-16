"""
anchor_worker/nonce_lock.py — enforces F6's "sole nonce authority" for
real: a Postgres advisory lock held on a dedicated connection for this
process's entire lifetime.

WHY THIS IS NEEDED ON TOP OF claim_batch's `FOR UPDATE SKIP LOCKED`
(anchor_worker/claim.py): SKIP LOCKED makes it safe for two anchor-worker
instances to coexist without CORRUPTING the outbox (no double-claiming
the same rows) — it does not stop them from racing each other for
nonces. Two instances could each successfully claim DIFFERENT rows in
the same round, build different batches, then both call
`w3.eth.get_transaction_count(signer.address, "pending")` around the
same moment and get back the SAME "next" nonce (a plain read-then-write
race, since neither has broadcast yet when the other reads) — one
transaction then fails or, depending on the RPC node, behaves
unpredictably. The plan's own architecture (§6.3) is explicit that
SKIP LOCKED exists to make an ACCIDENTAL second instance (e.g. an old
and new pod briefly overlapping during a rolling deploy) non-
catastrophic, not to make steady-state dual-writing a supported mode —
this lock is what actually enforces the "exactly one nonce authority"
design intent for a genuine second instance (a scaling mistake, a stuck
container that wasn't torn down), rather than leaving it as an
unenforced assumption in a comment.

Advisory locks are SESSION-scoped in Postgres, not transaction-scoped —
they only mean anything held on a connection that stays open, never
returned to a pool. This deliberately checks out its own dedicated
connection via the engine's connection pool (get_engine()) and never
returns it for the life of the process; the pool's max size must have at
least one spare connection for this (see config.py's database pool
sizing) on top of whatever the rest of the worker needs.

Releasing is automatic on process death of any kind (SIGKILL, OOM, crash)
— Postgres drops a session's advisory locks the moment its connection
closes, so there is no orphaned-lock recovery path to build or forget.
`release()` below is for the graceful-shutdown case only; it's an
optimization (a faster handoff to a replacement instance) not a
correctness requirement.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from db.engine import get_engine
from logging_config import get_logger

logger = get_logger(__name__)

# Two arbitrary but FIXED int4 keys identifying this specific lock, so it
# can never collide with some other feature's future use of
# pg_advisory_lock — the two-key form (vs. a single bigint) is just for
# readability here (the two halves loosely spell "Trus"/"tChn" in hex,
# purely for grep-ability, not a real scheme).
_LOCK_KEY_1 = 0x54727573
_LOCK_KEY_2 = 0x7443686E


class NonceAuthorityLock:
    """Call `acquire()` once at worker startup, before entering the main
    loop, and `release()` on graceful shutdown. Never share one instance
    across processes — each anchor-worker process must construct and
    hold its own."""

    def __init__(self):
        self._connection: AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        """Non-blocking: returns True if the lock was acquired (this
        process is now the sole nonce authority), False if another
        connection already holds it. Opens and keeps a dedicated
        connection only on success — a failed attempt returns its
        connection to the pool immediately rather than holding one open
        for nothing."""
        connection = await get_engine().connect()
        result = await connection.execute(
            text("SELECT pg_try_advisory_lock(:k1, :k2)"), {"k1": _LOCK_KEY_1, "k2": _LOCK_KEY_2}
        )
        acquired = result.scalar_one()
        if acquired:
            self._connection = connection
            return True
        await connection.close()
        return False

    async def release(self) -> None:
        if self._connection is None:
            return
        try:
            await self._connection.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": _LOCK_KEY_1, "k2": _LOCK_KEY_2}
            )
        finally:
            await self._connection.close()
            self._connection = None
