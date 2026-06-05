"""
agents/researcher.py  —  Agent 1: Researcher

Fixed from review:
  - Skips LLM synthesis if search failed (no point synthesising an error msg)
  - Logs full text hash, not truncated snippet
  - Proper type hint on bridge parameter
"""

import logging
from typing import Any, Optional

from langchain_tavily import TavilySearch
from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentState, get_llm, log_step, search_failed

logger = logging.getLogger(__name__)


def get_search_tool() -> TavilySearch:
    return TavilySearch(max_results=5)


async def researcher_node(state: AgentState, bridge: Optional[Any] = None) -> AgentState:
    """
    LangGraph node — Researcher agent.

    Steps:
      0. task_received   — log run start on-chain
      1. web_search      — Tavily search
      2. synthesise      — Groq LLM synthesis (skipped if search failed)
    """
    task   = state["task"]
    run_id = state["run_id"]
    llm    = get_llm()
    search = get_search_tool()

    tx_hashes:  list[str]  = list(state.get("tx_hashes",  []))
    sse_events: list[dict] = list(state.get("sse_events", []))

    logger.info("[Researcher] Starting — task: %s", task)

    # ── Step 0: log task receipt ──────────────────────────────────────────
    tx, evt = await log_step(
        bridge=bridge,
        agent_id="researcher",
        action="task_received",
        input_text=task,
        output_text=f"Researcher starting: {task}",
        step_index=0,
        run_id=run_id,
        trust_score=0,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 1: web search ────────────────────────────────────────────────
    logger.info("[Researcher] Running Tavily search...")
    search_ok = True
    try:
        results = await search.ainvoke({"query": task})
        if isinstance(results, list):
            raw_search = "\n\n".join(
                f"Source: {r.get('url', 'unknown')}\n{r.get('content', '')}"
                for r in results[:5]
            )
        else:
            raw_search = str(results)
    except Exception as e:
        logger.warning("[Researcher] Tavily error: %s", e)
        raw_search = f"Search unavailable: {e}"
        search_ok  = False

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="researcher",
        action="web_search",
        input_text=task,
        output_text=raw_search,          # full text hashed — not truncated
        step_index=1,
        run_id=run_id,
        trust_score=25,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 2: synthesise (skip if search failed) ────────────────────────
    if not search_ok or search_failed(raw_search):
        logger.warning("[Researcher] Skipping synthesis — search failed")
        research_output = (
            f"Research incomplete: web search failed for task '{task}'. "
            "Validator will flag this."
        )
    else:
        logger.info("[Researcher] Synthesising with LLM...")
        response = await llm.ainvoke([
            SystemMessage(content=(
                "You are the Researcher agent in TrustChain, a blockchain-verified multi-agent AI system. "
                "Synthesise web search results into clear, factual findings. "
                "Be concise and structured. Cite sources. "
                "Output 3-5 key findings as a numbered list."
            )),
            HumanMessage(content=(
                f"Task: {task}\n\n"
                f"Web search results:\n{raw_search}\n\n"
                "Key findings:"
            )),
        ])
        research_output = response.content

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="researcher",
        action="synthesis_complete",
        input_text=raw_search,           # full text hashed
        output_text=research_output,     # full output hashed
        step_index=2,
        run_id=run_id,
        trust_score=50,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    logger.info("[Researcher] Done — 3 steps logged on-chain")

    return {
        **state,
        "research":   research_output,
        "tx_hashes":  tx_hashes,
        "sse_events": sse_events,
        "messages":   [HumanMessage(content=research_output, name="researcher")],
    }