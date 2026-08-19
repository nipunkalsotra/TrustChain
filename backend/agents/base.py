"""
agents/base.py  —  shared plumbing for all 4 TrustChain agents

Imports are pointed at blockchain/client.py (your renamed blockchain.py)
"""

import secrets
from typing import TypedDict, Annotated, Any, Optional
from datetime import datetime, timezone

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from web3 import Web3

from agents.llm_provider import build_chat_model
from config import get_settings
from logging_config import get_logger
from pii_patterns import find_likely_pii

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Shared LangGraph state
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Inputs — set once at run start
    task:       str
    run_id:     str

    # Agent outputs — filled in as pipeline runs
    research:   str
    validation: str
    score:      int
    report:     str

    # Blockchain audit trail
    tx_hashes:  list[str]
    sse_events: list[dict]

    # Real cumulative LLM token usage across all 5 ainvoke() calls in this
    # run (researcher x1, validator x2, scorer x1, reporter x1) — see
    # track_token_usage below. Threaded through the same way tx_hashes/
    # sse_events are (each node reads the incoming total, adds its own
    # calls' usage, returns the new total).
    tokens_used: int

    # LangChain message history
    messages: Annotated[list[BaseMessage], add_messages]


# ─────────────────────────────────────────────────────────────────────────────
#  LLM singleton — provider-agnostic, see agents/llm_provider.py
# ─────────────────────────────────────────────────────────────────────────────

_llm: Optional[BaseChatModel] = None

def get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        _llm = build_chat_model()
        logger.info("llm_initialised", provider=get_settings().llm_provider)
    return _llm


class TokenBudgetExceeded(RuntimeError):
    """Raised by track_token_usage when a single run's cumulative LLM
    token usage crosses config.llm_token_budget_per_run — a per-run
    safety valve, not the org-level billing decision (that's checked
    once before the run starts — see main.py's run_agent handler and
    db.tenancy.get_org_token_budget_status). Propagates up through
    whichever LangGraph node raised it; agents/pipeline.py's existing
    top-level except Exception already turns any node failure into a
    normal {"type": "error", ...} SSE event and a failed run, so no new
    handling is needed at that layer."""


def track_token_usage(tokens_so_far: int, response: Any, run_id: str) -> int:
    """Extracts real token usage from an LLM response's usage_metadata
    (populated by langchain_groq for every ainvoke() call — see
    agents/llm_provider.py; for the Scorer's structured-output call this
    is result["raw"], the underlying AIMessage) and returns the run's new
    cumulative total. Takes the running total as a plain int, not
    AgentState, because a single node can make more than one LLM call
    (agents/validator.py makes two) — reading state["tokens_used"] again
    for the second call would miss the first call's usage, since the
    node's incoming `state` argument doesn't change mid-node.

    Raises TokenBudgetExceeded if the new total crosses the configured
    per-run ceiling — see its docstring for why this is deliberately
    loose (a well-behaved run, 5 calls with a 2048-token output cap
    each, never comes close)."""
    usage = getattr(response, "usage_metadata", None) or {}
    total = tokens_so_far + usage.get("total_tokens", 0)
    limit = get_settings().llm_token_budget_per_run
    if total > limit:
        raise TokenBudgetExceeded(f"run {run_id} exceeded its per-run LLM token budget ({total} > {limit} tokens)")
    return total


# ─────────────────────────────────────────────────────────────────────────────
#  log_step — durably records one agent step, returns SSE event
#
#  Phase 1 sent one blockchain transaction per step, synchronously, inline
#  with the pipeline (~13 tx/run). Phase 2 replaces that with the
#  transactional outbox pattern: this function writes a Step row and an
#  AnchorOutbox row in ONE database transaction, then returns immediately —
#  actual on-chain anchoring happens later, out of band, when the anchor
#  worker batches many steps' leaf hashes into a single Merkle root and
#  submits ONE transaction for the whole batch (see blockchain/merkle.py
#  and the anchor_worker package).
#
#  Writing both rows atomically is what makes this durable rather than
#  best-effort: a crash between "recorded the step" and "queued it for
#  anchoring" is impossible to observe from outside this function — either
#  both commit, or neither does. Compare to the Phase 1 approach, where a
#  crash between "chain call sent" and "SSE event delivered" really could
#  lose a step's record entirely.
# ─────────────────────────────────────────────────────────────────────────────

async def log_step(
    bridge: Any,
    agent_id: str,
    action: str,
    input_text: str,
    output_text: str,
    step_index: int,
    run_id: str,
    trust_score: int = 0,
    agent_code_hash: Optional[str] = None,
    project_id: Optional[int] = None,
) -> tuple[str, dict]:
    """
    Returns (tx_hash, sse_event) — same 2-tuple shape as Phase 1, so none of
    the 4 agent node files needed to change. `tx_hash` is now a placeholder
    ("pending:<outbox_id>"), not a real transaction hash: anchoring hasn't
    happened yet when this function returns. Callers that need the real
    on-chain proof once it lands should poll GET /runs/{run_id} or
    GET /audit-log — see the SSE event's `anchorStatus` field.

    `bridge` is accepted but unused here (kept for call-site compatibility
    with agents/researcher.py etc., which still pass it through) — nothing
    in this function talks to the chain directly anymore.

    SSE event shape — the original 9 fields are unchanged (frontend's
    isStepEvent() in lib/types.ts requires a *truthy* txHash to treat this
    as a step event, which the placeholder string satisfies); stepId/
    outboxId/anchorStatus are new, additive fields a JSON consumer that
    doesn't know about them will simply ignore:
    { agentId, action, txHash, step, inputHash, outputHash, trustScore,
      runId, timestamp, stepId, outboxId, anchorStatus }
    """
    from db.engine import get_sessionmaker
    from db.models import AnchorOutbox, Step
    from blockchain.merkle import leaf_hash as compute_leaf_hash_v1
    from blockchain.merkle import leaf_hash_v2 as compute_leaf_hash_v2
    import observability

    # Detection only, never redaction/rejection — see pii_patterns.py's
    # module docstring for why mutating or blocking this content before
    # hashing would break independent proof verification for every step,
    # not just PII-shaped ones. This just gives operators visibility
    # (metric + log line) into anchor payloads worth reviewing.
    for field_name, text in (("input", input_text), ("output", output_text)):
        for pii in find_likely_pii(text):
            observability.ANCHOR_PAYLOAD_PII_DETECTED_TOTAL.labels(kind=pii.kind, field=field_name).inc()
            logger.warning(
                "anchor_payload_likely_pii", run_id=run_id, agent_id=agent_id, action=action,
                field=field_name, kind=pii.kind,
            )

    now = int(datetime.now(timezone.utc).timestamp())
    input_hash  = "0x" + Web3.solidity_keccak(["string"], [input_text]).hex()
    output_hash = "0x" + Web3.solidity_keccak(["string"], [output_text]).hex()

    # Leaf schema v2 (Phase 3 §6.2): only used when the caller supplies its
    # own agent fingerprint — older SDKs/callers that don't pass
    # agent_code_hash get exactly the original v1 leaf, byte-for-byte, so
    # nothing about pre-Phase-3 anchoring behavior changes for them.
    leaf_schema_version = 2 if agent_code_hash else 1
    common_kwargs = dict(
        run_id_hash=bytes(Web3.keccak(text=run_id)),
        agent_id_hash=bytes(Web3.keccak(text=agent_id)),
        action_hash=bytes(Web3.keccak(text=action)),
        input_hash=bytes.fromhex(input_hash.removeprefix("0x")),
        output_hash=bytes.fromhex(output_hash.removeprefix("0x")),
        step_index=step_index,
        timestamp=now,
    )
    if leaf_schema_version == 2:
        leaf = compute_leaf_hash_v2(
            **common_kwargs, agent_code_hash=bytes.fromhex(agent_code_hash.removeprefix("0x")),
        )
    else:
        leaf = compute_leaf_hash_v1(**common_kwargs)
    leaf_hash_hex = "0x" + leaf.hex()

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        step = Step(
            run_id=run_id, agent_id=agent_id, step_index=step_index, action=action,
            input_hash=input_hash, output_hash=output_hash, leaf_hash=leaf_hash_hex,
            timestamp=now, created_at=now,
            agent_code_hash=agent_code_hash, leaf_schema_version=leaf_schema_version,
        )
        session.add(step)
        await session.flush()  # populates step.id without ending the transaction

        outbox = AnchorOutbox(step_id=step.id, next_attempt_at=now, created_at=now)
        session.add(outbox)

        await session.commit()  # steps + anchor_outbox commit together, or neither does
        step_id, outbox_id = step.id, outbox.id

    # Detector 1 (Phase 3 §6.2) — synchronous, on the write path, so
    # identity drift is caught in THIS request rather than waiting for a
    # periodic sweep. Runs AFTER the commit above, and never raises: the
    # step is anchored unconditionally regardless of the outcome here — a
    # tampered agent's actions must still be recorded (rejecting the write
    # would hand the attacker exactly the "unrecorded action" outcome this
    # whole system exists to prevent). Only checked when the caller
    # supplied both project_id and agent_code_hash — the internal 4-agent
    # pipeline (researcher/validator/scorer/reporter) doesn't register
    # on-chain identities per project the way SDK-instrumented third-party
    # agents do, so it has nothing to drift-check against.
    if project_id is not None and agent_code_hash is not None:
        try:
            await _check_identity_drift(project_id, agent_id, agent_code_hash, run_id, step_id, now)
        except Exception as e:
            logger.error("identity_drift_check_failed", run_id=run_id, agent_id=agent_id, error=str(e))

    tx_hash_placeholder = f"pending:{outbox_id}"

    sse_event = {
        "agentId":     agent_id,
        "action":      action,
        "txHash":      tx_hash_placeholder,
        "step":        step_index,
        "inputHash":   input_hash,
        "outputHash":  output_hash,
        "trustScore":  trust_score,
        "runId":       run_id,
        "timestamp":   now,
        "stepId":      step_id,
        "outboxId":    outbox_id,
        "anchorStatus": "pending",
    }

    logger.info(
        "step_recorded", agent_id=agent_id, action=action, step_index=step_index,
        step_id=step_id, outbox_id=outbox_id,
    )
    return tx_hash_placeholder, sse_event


async def _check_identity_drift(
    project_id: int, agent_id: str, presented_code_hash: str, run_id: str, step_id: int, now: int,
) -> None:
    """Detector 1 (Phase 3 §6.2). A single indexed read of the `agents`
    current-state table — no RPC, no chain call — comparing the hash the
    caller says its agent currently has against the hash that agent was
    actually REGISTERED with on-chain. A mismatch means the deployed
    model/version/system_prompt changed without going through
    register_agent()/update — a silent substitution, or a stale config
    the caller forgot to update. Not registered at all is deliberately
    NOT treated as drift here — an unregistered agent has nothing to
    drift FROM; that is a separate, weaker signal this function doesn't
    try to also cover."""
    from sqlalchemy import select
    from db.engine import get_sessionmaker
    from db.models import Agent

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(Agent).where(Agent.project_id == project_id, Agent.agent_id == agent_id)
        record = (await session.execute(stmt)).scalar_one_or_none()
        if record is None:
            return  # nothing registered to drift-check against

        if record.code_hash.lower() == presented_code_hash.lower():
            record.last_verified_at = now
            await session.commit()
            return

        record.last_drift_at = now
        await session.commit()
        registered_hash = record.code_hash

    import observability
    observability.INTEGRITY_CHECKS_TOTAL.labels(detector="identity_drift", result="mismatch").inc()

    from db.alerts import raise_alert
    from db.models import Project

    async with session_factory() as session:
        project = await session.get(Project, project_id)
    org_id = project.org_id if project else None
    if org_id is None:
        return

    await raise_alert(
        org_id=org_id, project_id=project_id, alert_type="agent_identity_drift", severity="warning",
        title=f"Agent '{agent_id}' logged a step under a different identity than registered",
        summary=(
            f"Step {step_id} in run {run_id} was logged with a code_hash that doesn't match "
            f"what '{agent_id}' is registered as on-chain for this project."
        ),
        subject=f"agent:{agent_id}",
        evidence={
            "agentId": agent_id, "runId": run_id, "stepId": step_id,
            "registeredHash": registered_hash, "presentedHash": presented_code_hash,
        },
        detector="identity_drift", now=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_run_id() -> str:
    """Generate a run ID: human-readable timestamp prefix (for eyeballing
    in logs/URLs) plus a short random suffix. The timestamp ALONE is only
    second-granularity — two runs starting in the same wall-clock second
    (real under any concurrent load, and now that multi-tenancy means
    many projects can each start a run at any moment, not a corner case)
    would otherwise collide: db.create_run's upsert would silently treat
    the second call as updating the first run's row instead of creating a
    genuinely separate one."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}_{secrets.token_hex(4)}"


def search_failed(raw: str) -> bool:
    """Returns True if Tavily search produced an error string instead of results."""
    return raw.startswith("Search unavailable:")