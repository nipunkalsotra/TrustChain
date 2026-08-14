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
    from blockchain.merkle import leaf_hash as compute_leaf_hash

    now = int(datetime.now(timezone.utc).timestamp())
    input_hash  = "0x" + Web3.solidity_keccak(["string"], [input_text]).hex()
    output_hash = "0x" + Web3.solidity_keccak(["string"], [output_text]).hex()

    leaf = compute_leaf_hash(
        run_id_hash=bytes(Web3.keccak(text=run_id)),
        agent_id_hash=bytes(Web3.keccak(text=agent_id)),
        action_hash=bytes(Web3.keccak(text=action)),
        input_hash=bytes.fromhex(input_hash.removeprefix("0x")),
        output_hash=bytes.fromhex(output_hash.removeprefix("0x")),
        step_index=step_index,
        timestamp=now,
    )
    leaf_hash_hex = "0x" + leaf.hex()

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        step = Step(
            run_id=run_id, agent_id=agent_id, step_index=step_index, action=action,
            input_hash=input_hash, output_hash=output_hash, leaf_hash=leaf_hash_hex,
            timestamp=now, created_at=now,
        )
        session.add(step)
        await session.flush()  # populates step.id without ending the transaction

        outbox = AnchorOutbox(step_id=step.id, next_attempt_at=now, created_at=now)
        session.add(outbox)

        await session.commit()  # steps + anchor_outbox commit together, or neither does
        step_id, outbox_id = step.id, outbox.id

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