"""
db/read_model.py — queries backing GET /audit-log, /trust-scores,
/trust-scores/history, /leaderboard, replacing what used to be live V1
BlockchainBridge RPC calls (blockchain/client.py) with reads against
Postgres tables the anchor worker/scorer/indexer keep up to date.

Every function here takes `project_id` and scopes its query through a join
to `runs.project_id` — this is invariant I7 (no tenant can read another
tenant's runs, agents, or scores) made real for the read model, not just
informally true because nobody's built a second tenant yet. A run_id that
exists but belongs to a different project is treated identically to a
run_id that doesn't exist at all (empty result, not a 403) — a caller
can't distinguish "not yours" from "never existed" by probing IDs.

/verify, /verify/tamper-demo, /verify-audit are deliberately NOT here and
stay on the live V1 bridge — their entire point is proving on-chain
immutability by re-reading the chain directly; routing them through a
cache would defeat that. They also have no tenant concept at all (V1 predates
multi-tenancy), which is a known, inherent limitation of keeping V1 around
for that one purpose — not a new gap this module introduces.

Response shapes match what blockchain/client.py's BlockchainBridge used to
return field-for-field where the underlying data still means the same
thing (frontend/lib/types.ts is the contract, unchanged by this migration)
— plus a few additive fields (anchorStatus, batchId) following the same
"new consumers can use them, old ones ignore them" pattern already used
for SSE events (see agents/base.py::log_step).
"""

from typing import Optional

from sqlalchemy import func, select

from db.engine import get_sessionmaker
from db.models import AnchorBatch, AnchorOutbox, ReadModelScore, Run, Step

AGENTS = ["researcher", "validator", "scorer", "reporter"]


# ── Audit log (steps + anchor_batches) ──────────────────────────────────

async def get_audit_log_entries(project_id: int, run_id: Optional[str] = None) -> list[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(Step, AnchorBatch.tx_hash, AnchorBatch.status, AnchorOutbox.id)
            .join(Run, Run.run_id == Step.run_id)
            .outerjoin(AnchorBatch, Step.anchor_batch_id == AnchorBatch.id)
            .outerjoin(AnchorOutbox, AnchorOutbox.step_id == Step.id)
            .where(Run.project_id == project_id)
            .order_by(Step.run_id, Step.step_index)
        )
        if run_id:
            stmt = stmt.where(Step.run_id == run_id)
        rows = (await session.execute(stmt)).all()

    entries = []
    for step, batch_tx_hash, batch_status, outbox_id in rows:
        if batch_status == "confirmed" and batch_tx_hash:
            tx_hash, anchor_status = batch_tx_hash, "confirmed"
        else:
            # Same placeholder convention as the SSE stream (see
            # agents/base.py::log_step) — truthy, so any consumer that
            # treats a falsy txHash as "not a real entry" stays correct.
            tx_hash = f"pending:{outbox_id}" if outbox_id is not None else ""
            anchor_status = batch_status or "pending"
        entries.append({
            "entryId":      step.id,
            "runId":        step.run_id,
            "agentId":      step.agent_id,
            "action":       step.action,
            "inputHash":    step.input_hash,
            "outputHash":   step.output_hash,
            "timestamp":    step.timestamp,
            "stepIndex":    step.step_index,
            "metadata":     step.metadata_json or "",
            "txSender":     None,  # meaningless per-step once batched — see module docstring
            "txHash":       tx_hash,
            "anchorStatus": anchor_status,
        })
    return entries


# ── Trust scores (rm_scores) ────────────────────────────────────────────

async def get_trust_scores(project_id: int, run_id: str) -> list[dict]:
    """Current (latest) score per agent for a run — matches
    BlockchainBridge.get_all_scores' shape: [{agentId, runId, score}]."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        latest_ids = (
            select(func.max(ReadModelScore.id))
            .join(Run, Run.run_id == ReadModelScore.run_id)
            .where(ReadModelScore.run_id == run_id, Run.project_id == project_id)
            .group_by(ReadModelScore.agent_id)
        )
        stmt = select(ReadModelScore).where(ReadModelScore.id.in_(latest_ids))
        rows = (await session.execute(stmt)).scalars().all()
    return [{"agentId": r.agent_id, "runId": r.run_id, "score": r.score} for r in rows]


async def get_trust_score_history(project_id: int, run_id: str) -> dict[str, list[dict]]:
    """Full history per agent for a run — matches
    BlockchainBridge.get_all_score_histories' shape: always includes all 4
    agent keys, even with an empty list, so the frontend never has to
    special-case a missing key."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(ReadModelScore)
            .join(Run, Run.run_id == ReadModelScore.run_id)
            .where(ReadModelScore.run_id == run_id, Run.project_id == project_id)
            .order_by(ReadModelScore.agent_id, ReadModelScore.id)
        )
        rows = (await session.execute(stmt)).scalars().all()

    history: dict[str, list[dict]] = {a: [] for a in AGENTS}
    for r in rows:
        history.setdefault(r.agent_id, []).append(
            {"score": r.score, "timestamp": r.timestamp, "reason": r.reason}
        )
    return history


async def get_leaderboard(project_id: int, max_runs: int = 50) -> dict:
    """
    Aggregates the most recent `max_runs` distinct runs' (within this
    project) latest-per-agent scores into avg/best/count per agent —
    matches BlockchainBridge.get_leaderboard's
    {agents, totalRuns, runsConsidered} shape. "Most recent" is by highest
    rm_scores.id within each run (insertion order), not run creation time
    — consistent with get_trust_scores' notion of "current" score.
    """
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        total_runs = (
            await session.execute(
                select(func.count(func.distinct(ReadModelScore.run_id)))
                .select_from(ReadModelScore)
                .join(Run, Run.run_id == ReadModelScore.run_id)
                .where(Run.project_id == project_id)
            )
        ).scalar_one()

        recent_run_ids_stmt = (
            select(ReadModelScore.run_id, func.max(ReadModelScore.id).label("last_id"))
            .join(Run, Run.run_id == ReadModelScore.run_id)
            .where(Run.project_id == project_id)
            .group_by(ReadModelScore.run_id)
            .order_by(func.max(ReadModelScore.id).desc())
            .limit(max_runs)
        )
        recent_run_ids = [row.run_id for row in (await session.execute(recent_run_ids_stmt)).all()]
        runs_considered = len(recent_run_ids)

        if not recent_run_ids:
            return {"agents": [], "totalRuns": total_runs, "runsConsidered": 0}

        latest_ids = (
            select(func.max(ReadModelScore.id))
            .where(ReadModelScore.run_id.in_(recent_run_ids))
            .group_by(ReadModelScore.run_id, ReadModelScore.agent_id)
        )
        stmt = select(ReadModelScore).where(ReadModelScore.id.in_(latest_ids))
        rows = (await session.execute(stmt)).scalars().all()

    scores_by_agent: dict[str, list[int]] = {}
    for r in rows:
        scores_by_agent.setdefault(r.agent_id, []).append(r.score)

    agents = [
        {
            "agentId":   agent_id,
            "avgScore":  round(sum(scores) / len(scores)),
            "bestScore": max(scores),
            "runsCount": len(scores),
        }
        for agent_id, scores in scores_by_agent.items()
    ]
    agents.sort(key=lambda a: a["avgScore"], reverse=True)

    return {"agents": agents, "totalRuns": total_runs, "runsConsidered": runs_considered}
