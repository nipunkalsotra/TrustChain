"""
indexer/scores.py — populates rm_scores from real TrustScoreRegistryV2
ScoreUpdated events.

Unlike anchor_batches (source of truth is Postgres, chain events only
reconcile confirmation status — see reconcile.py's docstring), rm_scores
is genuinely nothing but a replay of this event stream: every field the
row needs (agentId, runId, newScore, reason, timestamp) is right there in
the event, so indexing it is a straight insert, not a merge against
existing local state.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def index_score_updated(session: AsyncSession, event) -> bool:
    """Returns True if a new row was inserted, False if this (tx_hash,
    log_index) was already indexed (ON CONFLICT DO NOTHING — see the
    unique constraint on rm_scores, needed so a reorg-triggered cursor
    rewind can safely reprocess an overlapping range)."""
    args = event["args"]
    now = int(datetime.now(timezone.utc).timestamp())

    result = await session.execute(
        text("""
            INSERT INTO rm_scores
                (agent_id, run_id, score, reason, timestamp, block_number, tx_hash, log_index, indexed_at)
            VALUES (:agent_id, :run_id, :score, :reason, :timestamp, :block_number, :tx_hash, :log_index, :now)
            ON CONFLICT (tx_hash, log_index) DO NOTHING
            RETURNING id
        """),
        {
            "agent_id": args["agentId"],
            "run_id": args["runId"],
            "score": args["newScore"],
            "reason": args["reason"],
            "timestamp": args["timestamp"],
            "block_number": event["blockNumber"],
            "tx_hash": "0x" + event["transactionHash"].hex(),
            "log_index": event["logIndex"],
            "now": now,
        },
    )
    return result.first() is not None
