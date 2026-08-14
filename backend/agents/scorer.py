"""
agents/scorer.py  —  Agent 3: Scorer

Responsibilities:
  - Reads research + validation output
  - Computes 0-100 trust score for each agent via LLM
  - Writes scores to TrustScoreRegistryV2 on-chain (blockchain/score_writer.py)
  - Only agent that writes trust scores
"""

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import AgentState, get_llm, log_step
from blockchain.score_writer import write_score

logger = logging.getLogger(__name__)


class AgentScores(BaseModel):
    """Structured-output schema for the Scorer agent's LLM call — F18
    (Phase 2 plan's fix list) replaces regex-parsing the model's free-text
    response with its own native structured-output/tool-calling support.
    A malformed or out-of-range response now fails validation loudly
    (ValueError, which surfaces as a failed run — see main.py's
    _run_pipeline_background) instead of the old regex parser's silent
    fallback to a default score of 50, which could mask a real scoring
    failure as a plausible-looking result."""

    researcher: int = Field(ge=0, le=100, description="Trust score for the researcher agent")
    validator: int = Field(ge=0, le=100, description="Trust score for the validator agent")
    scorer: int = Field(ge=0, le=100, description="Trust score for the scorer agent itself")
    reporter: int = Field(ge=0, le=100, description="Trust score for the reporter agent")
    reasoning: str = Field(description="One-sentence explanation of the scores")


async def scorer_node(state: AgentState, bridge: Optional[Any] = None) -> AgentState:
    """
    LangGraph node — Scorer agent.

    Steps:
      0. scoring_started   — log start on-chain
      1. compute_scores    — LLM assigns scores for all 4 agents
      2. write_scores      — write_score() for each agent → TrustScoreRegistryV2
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

    # ── Step 1: LLM scoring, via structured output (F18) ──────────────────
    structured_llm = llm.with_structured_output(AgentScores, include_raw=True)
    result = await structured_llm.ainvoke([
        SystemMessage(content=(
            "You are the Scorer agent in TrustChain. "
            "Assign trust scores (0-100) for all 4 agents based on pipeline quality.\n\n"
            "Scoring rules:\n"
            "- researcher: depth, relevance, source quality of research\n"
            "- validator:  thoroughness of fact-checking, verdict clarity\n"
            "- scorer:     give yourself 80 (you are running correctly)\n"
            "- reporter:   pre-score 75 (will produce final report)"
        )),
        HumanMessage(content=(
            f"Task: {task}\n\n"
            f"Research:\n{research[:800]}\n\n"
            f"Validation:\n{validation[:800]}\n\n"
            "Assign scores:"
        )),
    ])

    parsed: Optional[AgentScores] = result["parsed"]
    if parsed is None:
        # No silent fallback to a default score (the old regex parser's
        # behavior) — a scoring failure should fail the run loudly, not
        # produce a plausible-looking but meaningless result. See
        # main.py's _run_pipeline_background: any exception here already
        # marks the run as failed/errored, which is the correct outcome
        # for a system whose entire point is trustworthy scoring.
        raise ValueError(f"Scorer LLM response failed structured-output validation: {result.get('parsing_error')}")

    raw_message = result["raw"]
    score_text = raw_message.content or parsed.model_dump_json()
    logger.info("[Scorer] Raw LLM scores:\n%s", score_text)

    agent_scores = {
        "researcher": parsed.researcher, "validator": parsed.validator,
        "scorer": parsed.scorer, "reporter": parsed.reporter,
    }
    logger.info("[Scorer] Parsed: %s (reasoning: %s)", agent_scores, parsed.reasoning)

    # ── Step 2: write to TrustScoreRegistryV2 on-chain ────────────────────
    score_txs: dict[str, str] = {}
    for agent_id, score_val in agent_scores.items():
        score_tx = await write_score(
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