"""
indexer/reconcile.py — reconciles anchor_batches/anchor_outbox from real
AgentAuditLogV2.BatchAnchored events.

submit_batch() already marks its OWN batch 'confirmed' synchronously,
having waited for its own receipt — so in the common case this module has
nothing to do. Its actual job is the crash window submit_batch can't cover
itself: if the anchor worker sends a transaction, it gets mined, and the
worker process dies before the receipt wait (or before committing the
resulting status update), that batch is stuck at status='submitted'
forever from the worker's own point of view — even though it fully
succeeded on-chain. BatchAnchored is chain ground truth; reconciling
against it is what actually closes that window, independent of whatever
state any particular worker process was in when it crashed.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import observability


async def reconcile_batch_anchored(session: AsyncSession, event) -> bool:
    """
    Returns True if a local row was reconciled, False if this event didn't
    match anything locally outstanding (already confirmed, or a batch this
    Postgres never knew about — both fine to skip).
    """
    merkle_root = "0x" + event["args"]["merkleRoot"].hex()
    tx_hash = "0x" + event["transactionHash"].hex()
    block_number = event["blockNumber"]
    block_hash = "0x" + event["blockHash"].hex()
    onchain_anchor_id = event["args"]["anchorId"]
    now = int(datetime.now(timezone.utc).timestamp())

    result = await session.execute(
        text("""
            UPDATE anchor_batches
            SET status = 'confirmed', tx_hash = :tx_hash, block_number = :block_number, block_hash = :block_hash,
                onchain_anchor_id = :onchain_anchor_id, confirmed_at = :now
            WHERE merkle_root = :merkle_root AND status != 'confirmed'
            RETURNING id
        """),
        {
            "tx_hash": tx_hash,
            "block_number": block_number,
            "block_hash": block_hash,
            "onchain_anchor_id": onchain_anchor_id,
            "now": now,
            "merkle_root": merkle_root,
        },
    )
    row = result.first()
    if row is None:
        return False

    batch_id = row.id
    await session.execute(
        text("UPDATE anchor_outbox SET status = 'anchored' WHERE batch_id = :batch_id AND status != 'anchored'"),
        {"batch_id": batch_id},
    )
    observability.INDEXER_RECONCILIATIONS_TOTAL.inc()
    return True
