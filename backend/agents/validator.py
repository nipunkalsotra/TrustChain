"""
agents/validator.py  —  Agent 2: Validator

Responsibilities:
  - Receives Researcher output
  - Fact-checks key claims via MCP fact_check tool (http://localhost:8001)
  - Produces validation verdict: PASS / PARTIAL / FAIL
  - Logs all steps to AgentAuditLog on Monad
"""

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentState, get_llm, log_step, search_failed

logger = logging.getLogger(__name__)


async def validator_node(state: AgentState, bridge: Optional[Any] = None, tools: list = None) -> AgentState:
    """
    LangGraph node — Validator agent.

    Steps:
      0. research_received  — log receipt on-chain
      1. extract_claims     — LLM pulls top 2 verifiable claims
      2. fact_check         — MCP fact_check tool to verify claims
      3. validation_report  — LLM produces PASS/PARTIAL/FAIL verdict
    """
    task       = state["task"]
    research   = state["research"]
    run_id     = state["run_id"]
    llm        = get_llm()

    # Resolve fact_check from injected MCP tools
    fact_check_tool = None
    if tools:
        fact_check_tool = next((t for t in tools if t.name == "fact_check"), None)
    if fact_check_tool is None:
        raise RuntimeError("fact_check MCP tool not found — is web_search server running on port 8001?")

    tx_hashes:  list[str]  = list(state.get("tx_hashes",  []))
    sse_events: list[dict] = list(state.get("sse_events", []))

    logger.info("[Validator] Starting validation...")

    # ── Step 0: log receipt ───────────────────────────────────────────────
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="validator",
        action="research_received",
        input_text=research,
        output_text="Validator starting fact-check",
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=50,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 1: extract claims ────────────────────────────────────────────
    logger.info("[Validator] Extracting claims...")
    claims_response = await llm.ainvoke([
        SystemMessage(content=(
            "Extract exactly 2 key factual claims from the research that can be independently "
            "verified via web search. Return only the 2 claims as a numbered list, nothing else."
        )),
        HumanMessage(content=research),
    ])
    claims_text = claims_response.content

    # ── Step 2: fact-check via MCP ────────────────────────────────────────
    logger.info("[Validator] Fact-checking via MCP fact_check tool...")
    search_ok = True
    try:
        # Use the first extracted claim as the primary claim to check
        primary_claim = claims_text.split("\n")[0].lstrip("1. ").strip()
        result = await fact_check_tool.ainvoke({
            "claim":   primary_claim,
            "context": task,
        })
        # MCP tool returns: { claim, verdict, confidence, evidence, summary }
        verdict    = result.get("verdict", "unverified")
        confidence = result.get("confidence", 0.5)
        evidence   = result.get("evidence", [])
        summary    = result.get("summary", "")

        fact_raw = (
            f"Verdict: {verdict} (confidence: {confidence:.0%})\n"
            f"Summary: {summary}\n\n"
            + "\n\n".join(
                f"Source: {e.get('url', 'unknown')}\n{e.get('content', '')}"
                for e in evidence[:3]
            )
        )
    except Exception as e:
        logger.warning("[Validator] MCP fact_check error: %s", e)
        fact_raw  = f"Search unavailable: {e}"
        search_ok = False

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="validator",
        action="fact_check",
        input_text=claims_text,
        output_text=fact_raw,
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=65,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 3: validation report ─────────────────────────────────────────
    logger.info("[Validator] Generating validation report...")

    fact_context = (
        fact_raw if search_ok
        else "Fact-check search unavailable — validation based on research quality only."
    )

    val_response = await llm.ainvoke([
        SystemMessage(content=(
            "You are the Validator agent in TrustChain. "
            "Compare the original research against fact-check results. "
            "Identify: (1) CONFIRMED claims, (2) UNCERTAIN claims, (3) ERRORS or hallucinations.\n\n"
            "End with a single line:\n"
            "VERDICT: PASS | PARTIAL | FAIL — <one sentence reason>"
        )),
        HumanMessage(content=(
            f"Original research:\n{research}\n\n"
            f"Fact-check results:\n{fact_context}\n\n"
            "Validation report:"
        )),
    ])
    validation_output = val_response.content

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="validator",
        action="validation_complete",
        input_text=fact_raw,
        output_text=validation_output,
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=75,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    logger.info("[Validator] Done — verdict: %s", validation_output[-100:])

    return {
        **state,
        "validation": validation_output,
        "tx_hashes":  tx_hashes,
        "sse_events": sse_events,
        "messages":   [HumanMessage(content=validation_output, name="validator")],
    }