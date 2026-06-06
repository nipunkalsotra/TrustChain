"""
agents/researcher.py  —  Agent 1: Researcher

Responsibilities:
  - Takes the user task
  - Runs web_search via MCP tool (http://localhost:8001)
  - Produces structured research output
  - Logs every step to AgentAuditLog on Monad
"""

import logging
from langchain_core.messages import HumanMessage, SystemMessage

from agents.base import AgentState, get_llm, log_step

logger = logging.getLogger(__name__)


async def researcher_node(state: AgentState, bridge=None, tools: list = None) -> AgentState:
    """
    LangGraph node for the Researcher agent.

    Steps:
      0. Announce start
      1. Run web_search via MCP tool
      2. Synthesise findings with Groq LLM
      3. Log both steps on-chain
    """
    task   = state["task"]
    run_id = state["run_id"]
    llm    = get_llm()

    # Resolve search_web from injected MCP tools
    search_tool = None
    if tools:
        search_tool = next((t for t in tools if t.name == "search_web"), None)
    if search_tool is None:
        raise RuntimeError("search_web MCP tool not found — is web_search server running on port 8001?")

    tx_hashes  = list(state.get("tx_hashes",  []))
    sse_events = list(state.get("sse_events", []))

    logger.info("[Researcher] Starting task: %s", task)

    # ── Step 0: log task receipt ───────────────────────────────────────────
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

    # ── Step 1: web search via MCP ─────────────────────────────────────────
    logger.info("[Researcher] Running web_search via MCP...")
    try:
        result = await search_tool.ainvoke({"query": task, "max_results": 5})
        # MCP tool returns dict: { query, results: [{title, url, content, score}], answer }
        if isinstance(result, dict):
            items = result.get("results", [])
            raw_search = "\n\n".join(
                f"Source: {r.get('url', 'unknown')}\n{r.get('content', '')}"
                for r in items[:5]
            )
            if result.get("answer"):
                raw_search = f"Summary: {result['answer']}\n\n{raw_search}"
        else:
            raw_search = str(result)
    except Exception as e:
        logger.warning("[Researcher] MCP search_web error: %s", e)
        raw_search = f"Search unavailable: {e}"

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="researcher",
        action="web_search",
        input_text=task,
        output_text=raw_search[:500],
        step_index=1,
        run_id=run_id,
        trust_score=25,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    # ── Step 2: synthesise with LLM ───────────────────────────────────────
    logger.info("[Researcher] Synthesising with LLM...")
    synthesis_prompt = [
        SystemMessage(content=(
            "You are the Researcher agent in a multi-agent AI system called TrustChain. "
            "Your job is to synthesise web search results into clear, factual research findings. "
            "Be concise, structured, and cite sources where possible. "
            "Output 3-5 key findings as a numbered list."
        )),
        HumanMessage(content=(
            f"Task: {task}\n\n"
            f"Web search results:\n{raw_search}\n\n"
            "Synthesise the key findings:"
        )),
    ]

    response = await llm.ainvoke(synthesis_prompt)
    research_output = response.content

    tx, evt = await log_step(
        bridge=bridge,
        agent_id="researcher",
        action="synthesise_findings",
        input_text=raw_search[:300],
        output_text=research_output[:500],
        step_index=2,
        run_id=run_id,
        trust_score=50,
    )
    tx_hashes.append(tx)
    sse_events.append(evt)

    logger.info("[Researcher] Complete. %d steps logged.", 3)

    return {
        **state,
        "research":   research_output,
        "tx_hashes":  tx_hashes,
        "sse_events": sse_events,
        "messages":   [HumanMessage(content=research_output, name="researcher")],
    }