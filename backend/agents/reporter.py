"""
agents/reporter.py  —  Agent 4: Reporter

Responsibilities:
  - Reads research + validation + trust scores
  - Produces the final human-readable report shown in the UI
  - Logs final step to AgentAuditLog on Monad
"""

import logging
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentState, get_llm, log_step

logger = logging.getLogger(__name__)


async def reporter_node(state: AgentState, bridge: Optional[Any] = None) -> AgentState:
    """
    LangGraph node — Reporter agent.

    Steps:
      0. report_started   — log start on-chain
      1. generate_report  — LLM produces structured final report
      2. report_complete  — log completion on-chain
    """
    task       = state["task"]
    research   = state["research"]
    validation = state["validation"]
    score      = state.get("score", 0)
    run_id     = state["run_id"]
    llm        = get_llm()

    tx_hashes:  list[str]  = list(state.get("tx_hashes",  []))
    sse_events: list[dict] = list(state.get("sse_events", []))

    logger.info("[Reporter] Generating final report...")

    # ── Step 0: log start ─────────────────────────────────────────────────
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="reporter",
        action="report_started",
        input_text=validation,
        output_text="Reporter generating final summary",
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=score,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 1: generate report ───────────────────────────────────────────
    report_response = await llm.ainvoke([
        SystemMessage(content=(
            "You are the Reporter agent in TrustChain — a blockchain-verified multi-agent AI system.\n"
            "Produce a clean, structured final report for the judge to read.\n\n"
            "Use EXACTLY this format:\n\n"
            "## Summary\n"
            "<2-3 sentence executive summary>\n\n"
            "## Key Findings\n"
            "<numbered list of 3-5 findings from the research>\n\n"
            "## Validation Status\n"
            "<one paragraph — what was validated, what the verdict was>\n\n"
            "## Trust Score\n"
            f"Pipeline trust score: {score}/100\n"
            "<one sentence on what this score means>\n\n"
            "## Blockchain Audit\n"
            "All agent steps are permanently recorded on Monad testnet. "
            "Every action is immutably logged and verifiable via transaction hash."
        )),
        HumanMessage(content=(
            f"Task: {task}\n\n"
            f"Research findings:\n{research}\n\n"
            f"Validation report:\n{validation}\n\n"
            "Generate the final report:"
        )),
    ])
    report_output = report_response.content

    # ── Step 2: log completion ────────────────────────────────────────────
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="reporter",
        action="report_complete",
        input_text=task,
        output_text=report_output,
        step_index=len(tx_hashes),
        run_id=run_id,
        trust_score=score,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    logger.info("[Reporter] Done — report length: %d chars", len(report_output))

    return {
        **state,
        "report":     report_output,
        "tx_hashes":  tx_hashes,
        "sse_events": sse_events,
        "messages":   [HumanMessage(content=report_output, name="reporter")],
    }