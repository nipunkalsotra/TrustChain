"""
agents/base.py  —  shared plumbing for all 4 TrustChain agents

Imports are pointed at blockchain/client.py (your renamed blockchain.py)
"""

import os
import asyncio
import logging
from typing import TypedDict, Annotated, Any, Optional
from datetime import datetime

from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


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
#  Groq LLM singleton
# ─────────────────────────────────────────────────────────────────────────────

_llm: Optional[ChatGroq] = None

def get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set in .env")
        _llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            max_tokens=2048,
            api_key=api_key,
        )
        logger.info("Groq LLM initialised (llama-3.3-70b-versatile)")
    return _llm


# ─────────────────────────────────────────────────────────────────────────────
#  log_step — writes one agent step to chain and returns SSE event
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
    1. Writes the step to AgentAuditLog on Monad (full text hashed, not truncated)
    2. Returns (tx_hash, sse_event) in the Phase 0 locked shape

    SSE event shape:
    { agentId, action, txHash, step, inputHash, outputHash, trustScore, runId, timestamp }
    """
    # Lazy import to avoid circular deps
    if bridge is None:
        from blockchain.client import get_bridge
        bridge = get_bridge()

    # Full text is hashed on-chain — no truncation
    tx_hash = await bridge.log_action(
        agent_id=agent_id,
        action=action,
        input_text=input_text,
        output_text=output_text,
        step_index=step_index,
    )

    # Build SSE event
    sse_event = {
        "agentId":    agent_id,
        "action":     action,
        "txHash":     tx_hash,
        "step":       step_index,
        "inputHash":  "0x" + Web3.solidity_keccak(["string"], [input_text]).hex(),
        "outputHash": "0x" + Web3.solidity_keccak(["string"], [output_text]).hex(),
        "trustScore": trust_score,
        "runId":      run_id,
        "timestamp":  int(datetime.utcnow().timestamp()),
    }

    logger.info("[%s] %s step=%d tx=%s", agent_id, action, step_index, tx_hash[:20])
    return tx_hash, sse_event


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_run_id() -> str:
    """Generate a unique run ID for each demo run."""
    return f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"


def search_failed(raw: str) -> bool:
    """Returns True if Tavily search produced an error string instead of results."""
    return raw.startswith("Search unavailable:")