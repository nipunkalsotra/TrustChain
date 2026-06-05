"""
agents/scorer.py  —  Agent 3: Scorer

Responsibilities:
  - Reads research + validation output
  - Computes 0-100 trust score for each agent via LLM
  - Writes scores to TrustScoreRegistry on Monad
  - Only agent that calls bridge.update_score()
"""

import re
import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentState, get_llm, log_step

logger = logging.getLogger(__name__)


def _parse_agent_score(text: str, agent: str) -> int:
    """
    Parse score for a specific agent from LLM output.
    Tries 'researcher SCORE: 87' format first, falls back to generic number.
    """
    # Pattern: "researcher SCORE: 87" (case-insensitive)
    m = re.search(rf'{agent}\s+SCORE\s*:\s*(\d{{1,3}})', text, re.IGNORECASE)
    if m:
        return min(100, max(0, int(m.group(1))))

    # Fallback: last standalone 0-100 number in text
    numbers = re.findall(r'\b(\d{1,3})\b', text)
    for n in reversed(numbers):
        val = int(n)
        if 0 <= val <= 100:
            return val

    return 50  # safe default


async def scorer_node(state: AgentState, bridge: Optional[Any] = None) -> AgentState:
    """
    LangGraph node — Scorer agent.

    Steps:
      0. scoring_started   — log start on-chain
      1. compute_scores    — LLM assigns scores for all 4 agents
      2. write_scores      — bridge.update_score() for each agent → TrustScoreRegistry
      3. scores_written    — log summary on-chain
    """
    task       = state["task"]
    research   = state["research"]
    validation = state["validation"]
    run_id     = state["run_id"]
    llm        = get_llm()

    # Resolve bridge early — scorer needs it for update_score calls
    if bridge is None:
        from blockchain.client import get_bridge
        bridge = get_bridge()

    tx_hashes:  list[str]  = list(state.get("tx_hashes",  []))
    sse_events: list[dict] = list(state.get("sse_events", []))

    logger.info("[Scorer] Computing trust scores...")

    # ── Step 0: log start ─────────────────────────────────────────────────
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="scorer",
        action="scoring_started",
        input_text=validation,
        output_text="Scorer computing trust scores for all agents",
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=75,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 1: LLM scoring ───────────────────────────────────────────────
    score_response = await llm.ainvoke([
        SystemMessage(content=(
            "You are the Scorer agent in TrustChain. "
            "Assign trust scores (0-100) for all 4 agents based on pipeline quality.\n\n"
            "Scoring rules:\n"
            "- researcher: depth, relevance, source quality of research\n"
            "- validator:  thoroughness of fact-checking, verdict clarity\n"
            "- scorer:     give yourself 80 (you are running correctly)\n"
            "- reporter:   pre-score 75 (will produce final report)\n\n"
            "IMPORTANT — output EXACTLY this format:\n"
            "researcher SCORE: <0-100>\n"
            "validator SCORE: <0-100>\n"
            "scorer SCORE: <0-100>\n"
            "reporter SCORE: <0-100>\n"
            "Reasoning: <one sentence>"
        )),
        HumanMessage(content=(
            f"Task: {task}\n\n"
            f"Research:\n{research[:800]}\n\n"
            f"Validation:\n{validation[:800]}\n\n"
            "Assign scores:"
        )),
    ])
    score_text = score_response.content
    logger.info("[Scorer] Raw LLM scores:\n%s", score_text)

    # ── Parse scores ──────────────────────────────────────────────────────
    agents = ["researcher", "validator", "scorer", "reporter"]
    agent_scores = {a: _parse_agent_score(score_text, a) for a in agents}
    logger.info("[Scorer] Parsed: %s", agent_scores)

    # ── Step 2: write to TrustScoreRegistry on-chain ─────────────────────
    score_txs: dict[str, str] = {}
    for agent_id, score_val in agent_scores.items():
        score_tx = await bridge.update_score(
            agent_id=agent_id,
            run_id=run_id,
            score=score_val,
            reason="pipeline_scoring",
        )
        score_txs[agent_id] = score_tx
        logger.info("[Scorer] %s = %d → %s", agent_id, score_val, score_tx[:20])

    # Add score tx hashes to audit trail
    tx_hashes.extend(score_txs.values())

    # ── Step 3: log completion ────────────────────────────────────────────
    scores_summary = " | ".join(f"{k}={v}" for k, v in agent_scores.items())
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="scorer",
        action="scores_written",
        input_text=score_text,
        output_text=scores_summary,
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=agent_scores.get("scorer", 80),
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    final_score = agent_scores.get("researcher", 50)
    logger.info("[Scorer] Done — pipeline score: %d", final_score)

    return {
        **state,
        "score":      final_score,
        "tx_hashes":  tx_hashes,
        "sse_events": sse_events,
        "messages":   [HumanMessage(content=scores_summary, name="scorer")],
    }