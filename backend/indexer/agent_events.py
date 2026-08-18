"""
indexer/agent_events.py — populates rm_agent_events (append-only event
log) AND agents (current materialized state, upserted) from real
AgentIdentityRegistryV2 AgentRegistered / AgentUpdated / AgentRevoked /
IntegrityViolation events.

rm_agent_events indexing: same shape as indexer/scores.py's
index_score_updated — each event carries its own full row content, so
indexing is a straight insert (ON CONFLICT DO NOTHING keyed on
(tx_hash, log_index), so a reorg-triggered cursor rewind can safely
reprocess an overlapping block range without double-inserting).

agents indexing is deliberately NOT built from event args the way
rm_agent_events is — neither AgentRegistered NOR AgentUpdated actually
carries modelName/modelVersion (check AgentIdentityRegistryV2.sol's own
event definitions), so a field-by-field mapping from event args would
leave those columns permanently empty. Instead, whenever any of the
three identity-changing events fires, this does ONE live getAgent()
read (a `view` call, real chain data, not an estimate) and upserts the
FULL current record. This is correct specifically BECAUSE `agents` is a
current-state snapshot, not a history: a live read always returns the
chain's actual present truth, so the row converges to correct the
moment indexing catches up — unlike rm_agent_events (which must reflect
each event's own point-in-time content and would be WRONG to backfill
from a live read), reading live state here doesn't compromise
invariant I6's "rebuildable from genesis" property, since re-deriving
"what's true right now" is exactly what genesis replay is FOR when the
target is current state rather than history.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import observability


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


async def _sync_current_state(session: AsyncSession, project_id: int, agent_id: str) -> None:
    """Live getAgent() read + upsert into `agents` — see this module's
    own docstring for why a live read is the right source here, unlike
    everywhere else in the indexer."""
    from indexer.chain import get_identity_registry_contract

    contract = get_identity_registry_contract()
    record = await asyncio.to_thread(lambda: contract.functions.getAgent(project_id, agent_id).call())
    # AgentRecord: (projectId, agentId, codeHash, registeredBy, registeredAt, isActive, modelName, modelVersion)
    now = int(datetime.now(timezone.utc).timestamp())
    await session.execute(
        text("""
            INSERT INTO agents
                (project_id, agent_id, code_hash, model, version, registered_by, registered_at, is_active, updated_at)
            VALUES
                (:project_id, :agent_id, :code_hash, :model, :version, :registered_by, :registered_at, :is_active, :now)
            ON CONFLICT (project_id, agent_id) DO UPDATE SET
                code_hash = EXCLUDED.code_hash,
                model = EXCLUDED.model,
                version = EXCLUDED.version,
                registered_by = EXCLUDED.registered_by,
                registered_at = EXCLUDED.registered_at,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
        """),
        {
            "project_id": project_id, "agent_id": agent_id,
            "code_hash": "0x" + record[2].hex(), "model": record[6], "version": record[7],
            "registered_by": record[3], "registered_at": record[4], "is_active": record[5],
            "now": now,
        },
    )


async def _org_id_for_project(session: AsyncSession, project_id: int):
    result = await session.execute(text("SELECT org_id FROM projects WHERE id = :pid"), {"pid": project_id})
    return result.scalar_one_or_none()


async def _raise_identity_alert(
    session: AsyncSession, *, project_id: int, agent_id: str, alert_type: str, severity: str,
    title: str, summary: str, evidence: dict, now: int,
) -> None:
    """Detector 2 (Phase 3 §6.3) — EVERY identity-changing event raises an
    alert, unconditionally, including sanctioned ones. This is deliberate,
    not noise: the system cannot tell a legitimate re-registration apart
    from a stolen-key one at index time — that judgment belongs to a
    human, and an alert that was never surfaced is exactly the miss this
    detector exists to prevent.

    NOTE on the CI gate's "change reason" (Phase 3 §11): AgentUpdated's
    on-chain event args carry no free-text field for it (see
    AgentIdentityRegistryV2.sol's event definitions) — a reason string
    passed to `trustchain agents sync-manifest --reason "..."` is NOT currently
    threaded into this alert's evidence. It's visible in the CI job's own
    logs and in POST /agents' access log line, which is enough to
    correlate a specific alert against a specific deploy by timestamp, but
    reading the reason FROM the alert itself would need either a contract
    change (a new indexed event field) or an off-chain side-table keyed on
    tx_hash — both out of scope for this pass; tracked as a real gap, not
    silently assumed done."""
    org_id = await _org_id_for_project(session, project_id)
    if org_id is None:
        return  # project no longer exists (soft-deleted or a data anomaly) — nothing to notify
    from db.alerts import raise_alert
    await raise_alert(
        org_id=org_id, project_id=project_id, alert_type=alert_type, severity=severity,
        title=title, summary=summary, subject=f"agent:{agent_id}", evidence=evidence,
        detector="identity_events", now=now,
    )


async def index_agent_registered(session: AsyncSession, event) -> bool:
    args = event["args"]
    inserted = await _insert(session, event, "AgentRegistered", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "actor": args["registeredBy"],
        "code_hash": "0x" + args["codeHash"].hex(),
        "timestamp": args["timestamp"],
    })
    if inserted:
        await _sync_current_state(session, args["projectId"], args["agentId"])
    return inserted


async def index_agent_updated(session: AsyncSession, event) -> bool:
    """AgentUpdated fires when an already-registered agent_id is
    re-registered with a new codeHash (and possibly new model/version —
    see this module's docstring for why those come from a live read,
    not this event's own args)."""
    args = event["args"]
    inserted = await _insert(session, event, "AgentUpdated", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "code_hash": "0x" + args["newCodeHash"].hex(),
        "timestamp": args["timestamp"],
    })
    if inserted:
        await _sync_current_state(session, args["projectId"], args["agentId"])
        await _raise_identity_alert(
            session, project_id=args["projectId"], agent_id=args["agentId"],
            alert_type="agent_identity_changed", severity="warning",
            title=f"Agent '{args['agentId']}' identity was updated",
            summary=f"'{args['agentId']}' was re-registered with a new code hash.",
            evidence={
                "agentId": args["agentId"], "newHash": "0x" + args["newCodeHash"].hex(),
                "txHash": "0x" + event["transactionHash"].hex(), "blockNumber": event["blockNumber"],
            },
            now=args["timestamp"],
        )
    return inserted


async def index_agent_revoked(session: AsyncSession, event) -> bool:
    args = event["args"]
    inserted = await _insert(session, event, "AgentRevoked", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "actor": args["revokedBy"],
        "timestamp": args["timestamp"],
    })
    if inserted:
        await _sync_current_state(session, args["projectId"], args["agentId"])
        await _raise_identity_alert(
            session, project_id=args["projectId"], agent_id=args["agentId"],
            alert_type="agent_revoked", severity="warning",
            title=f"Agent '{args['agentId']}' was revoked",
            summary=f"'{args['agentId']}' is no longer an active registered identity.",
            evidence={
                "agentId": args["agentId"], "actor": args["revokedBy"],
                "txHash": "0x" + event["transactionHash"].hex(), "blockNumber": event["blockNumber"],
            },
            now=args["timestamp"],
        )
    return inserted


async def index_integrity_violation(session: AsyncSession, event) -> bool:
    args = event["args"]
    inserted = await _insert(session, event, "IntegrityViolation", {
        "project_id": args["projectId"],
        "agent_id": args["agentId"],
        "expected_hash": "0x" + args["expectedHash"].hex(),
        "provided_hash": "0x" + args["providedHash"].hex(),
        "timestamp": args["timestamp"],
    })
    if inserted:
        observability.AGENT_INTEGRITY_VIOLATIONS_TOTAL.inc()
        # Critical, not warning — unlike AgentUpdated/AgentRevoked above,
        # this event is the CONTRACT ITSELF reporting a hash mismatch
        # (verifyAgentAndLog found the provided config hash didn't match
        # what's registered) — as authoritative a tamper signal as this
        # system can produce, see AgentIdentityRegistryV2.sol's own
        # docstring on why this event exists at all.
        await _raise_identity_alert(
            session, project_id=args["projectId"], agent_id=args["agentId"],
            alert_type="onchain_integrity_violation", severity="critical",
            title=f"On-chain integrity violation for agent '{args['agentId']}'",
            summary=f"verifyAgentAndLog() found a config hash mismatch for '{args['agentId']}'.",
            evidence={
                "agentId": args["agentId"], "expectedHash": "0x" + args["expectedHash"].hex(),
                "providedHash": "0x" + args["providedHash"].hex(),
                "txHash": "0x" + event["transactionHash"].hex(), "blockNumber": event["blockNumber"],
            },
            now=args["timestamp"],
        )
    return inserted
