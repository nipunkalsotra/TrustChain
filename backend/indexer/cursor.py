"""
indexer/cursor.py — per-contract progress tracking with reorg detection.

Each contract the indexer follows gets one indexer_cursor row: the last
block fully processed, and that block's hash at the time it was processed.
Before trusting "resume from last_block + 1", the indexer re-fetches the
actual chain block at last_block and compares hashes — a mismatch means
the chain reorganized under it, and the blocks it already processed may
no longer be canonical. Anvil (single-node, no forks) and Monad testnet
under normal operation won't exercise this often, but the check costs one
extra RPC call per poll and is what makes "resume from a stored cursor"
actually safe rather than merely convenient.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

# How far back to rewind on a detected reorg. Deep enough to clear any
# reorg Anvil/Monad would plausibly produce; shallow enough that a rewind
# reprocesses a small, bounded range rather than walking back to genesis.
REORG_REWIND_BLOCKS = 12


async def get_cursor(session: AsyncSession, contract_name: str) -> dict | None:
    result = await session.execute(
        text("SELECT contract_name, last_block, last_block_hash FROM indexer_cursor WHERE contract_name = :name"),
        {"name": contract_name},
    )
    row = result.first()
    return dict(row._mapping) if row else None


async def save_cursor(session: AsyncSession, contract_name: str, last_block: int, last_block_hash: str) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    await session.execute(
        text("""
            INSERT INTO indexer_cursor (contract_name, last_block, last_block_hash, updated_at)
            VALUES (:name, :last_block, :last_block_hash, :now)
            ON CONFLICT (contract_name) DO UPDATE SET
                last_block = EXCLUDED.last_block,
                last_block_hash = EXCLUDED.last_block_hash,
                updated_at = EXCLUDED.updated_at
        """),
        {"name": contract_name, "last_block": last_block, "last_block_hash": last_block_hash, "now": now},
    )


async def resolve_start_block(session: AsyncSession, w3: Web3, contract_name: str, deploy_block: int = 0) -> int:
    """
    Returns the block number to resume scanning FROM (inclusive). No
    cursor yet -> start at `deploy_block` (0 is safe but slow for a chain
    with real history; callers following a freshly-deployed local Anvil
    pass 0 since the whole chain only has a few dozen blocks). A cursor
    whose recorded block hash no longer matches the chain's actual hash
    at that height means a reorg happened since the last poll — rewind
    by REORG_REWIND_BLOCKS and resume from there instead of trusting
    a cursor built on since-orphaned blocks.
    """
    cursor = await get_cursor(session, contract_name)
    if cursor is None:
        return deploy_block

    try:
        actual_block = await asyncio.to_thread(w3.eth.get_block, cursor["last_block"])
    except Exception:
        # Block no longer exists at that height at all (e.g. a very deep
        # reorg on a short local chain) — safest is the same rewind path.
        return max(deploy_block, cursor["last_block"] - REORG_REWIND_BLOCKS)

    actual_hash = "0x" + actual_block["hash"].hex()
    if actual_hash == cursor["last_block_hash"]:
        return cursor["last_block"] + 1

    return max(deploy_block, cursor["last_block"] - REORG_REWIND_BLOCKS)
