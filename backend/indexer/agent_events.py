"""
indexer/agent_events.py — populates rm_agent_events from real
AgentIdentityRegistryV2 AgentRegistered / AgentRevoked / IntegrityViolation
events.

Same shape as indexer/scores.py's index_score_updated: each event carries
its own full row content, so indexing is a straight insert (ON CONFLICT DO
NOTHING keyed on (tx_hash, log_index), so a reorg-triggered cursor rewind
can safely reprocess an overlapping block range without double-inserting).
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _insert(session: AsyncSession, event, event_type: str, fields: dict) -> bool:
    now = int(datetime.now(timezone.utc).timestamp())
    result = await session.execute(
        text("""
            INSERT INTO rm_agent_events
                (event_type, project_id, agent_id, actor, code_hash, expected_hash, provided_hash,
                 timestamp, block_number, tx_hash, log_index, indexed_at)
            VALUES
                (:event_type, :project_id, :agent_id, :actor, :code_hash, :expected_hash, :provided_hash,
                 :timestamp, :block_number, :tx_hash, :log_index, :now)
            ON CONFLICT (tx_hash, log_index) DO NOTHING
            RETURNING id
        """),
        {
            "event_type": event_type,
            "project_id": fields["project_id"],
            "agent_id": fields["agent_id"],
            "actor": fields.get("actor"),
            "code_hash": fields.get("code_hash"),
            "expected_hash": fields.get("expected_hash"),
            "provided_hash": fields.get("provided_hash"),
            "timestamp": fields["timestamp"],
            "block_number": event["blockNumber"],
            "tx_hash": "0x" + event["transactionHash"].hex(),
            "log_index": event["logIndex"],
            "now": now,
        },
    )
    return result.first() is not None


async def index_agent_registered(session: AsyncSession, event) -> bool:
    args = event["args"]
    return await _insert(session, event, "AgentRegistered", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "actor": args["registeredBy"],
        "code_hash": "0x" + args["codeHash"].hex(),
        "timestamp": args["timestamp"],
    })


async def index_agent_revoked(session: AsyncSession, event) -> bool:
    args = event["args"]
    return await _insert(session, event, "AgentRevoked", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "actor": args["revokedBy"],
        "timestamp": args["timestamp"],
    })


async def index_integrity_violation(session: AsyncSession, event) -> bool:
    args = event["args"]
    return await _insert(session, event, "IntegrityViolation", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "expected_hash": "0x" + args["expectedHash"].hex(),
        "provided_hash": "0x" + args["providedHash"].hex(),
        "timestamp": args["timestamp"],
    })
