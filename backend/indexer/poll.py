"""
indexer/poll.py — generic "follow one contract's one event type" loop body.

Both AgentAuditLogV2.BatchAnchored (-> reconcile.py) and
TrustScoreRegistryV2.ScoreUpdated (-> scores.py) follow the identical
shape: resolve where to resume from (cursor.py, reorg-aware), fetch every
log in [start, latest], hand each to a handler, then persist the new
cursor — so that shape lives here once instead of twice.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

import observability
from indexer.cursor import resolve_start_block, save_cursor
from logging_config import get_logger

logger = get_logger(__name__)


async def poll_once(
    session: AsyncSession,
    w3: Web3,
    contract,
    event_name: str,
    handler,
    cursor_name: str,
    deploy_block: int = 0,
) -> int:
    """Returns the number of events actually handled (a handler returning
    False for "already applied" doesn't count) — 0 means nothing new,
    used by main.py to decide whether to poll again immediately or wait."""
    start_block = await resolve_start_block(session, w3, cursor_name, deploy_block)
    latest_block = await asyncio.to_thread(lambda: w3.eth.block_number)
    observability.INDEXER_POLL_LAG_BLOCKS.set(max(0, latest_block - start_block))
    if start_block > latest_block:
        return 0

    event = getattr(contract.events, event_name)
    logs = await asyncio.to_thread(event.get_logs, from_block=start_block, to_block=latest_block)

    handled = 0
    for log in logs:
        if await handler(session, log):
            handled += 1
            observability.INDEXER_EVENTS_PROCESSED_TOTAL.labels(event_type=event_name).inc()

    latest_block_obj = await asyncio.to_thread(w3.eth.get_block, latest_block)
    latest_hash = "0x" + latest_block_obj["hash"].hex()
    await save_cursor(session, cursor_name, latest_block, latest_hash)
    await session.commit()

    if handled:
        logger.info("indexer_polled", cursor=cursor_name, from_block=start_block, to_block=latest_block, handled=handled)
    return handled
