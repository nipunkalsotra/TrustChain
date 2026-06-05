"""
agents/pipeline.py  —  LangGraph pipeline

Wires 4 agents in sequence:
  researcher → validator → scorer → reporter

Usage from FastAPI:
    from agents.pipeline import run_pipeline
    async for event in run_pipeline(task, run_id, bridge):
        yield event   # SSE event dict
"""

import logging
from typing import AsyncGenerator, Optional, Any

from langgraph.graph import StateGraph, END

from agents.base import AgentState, make_run_id
from agents.researcher import researcher_node
from agents.validator   import validator_node
from agents.scorer      import scorer_node
from agents.reporter    import reporter_node

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Build the graph
# ─────────────────────────────────────────────────────────────────────────────

def build_graph(bridge: Optional[Any] = None):
    """
    Compile the LangGraph StateGraph.
    bridge is injected via closure so nodes stay pure functions.
    """
    async def _researcher(state): return await researcher_node(state, bridge)
    async def _validator(state):  return await validator_node(state,  bridge)
    async def _scorer(state):     return await scorer_node(state,     bridge)
    async def _reporter(state):   return await reporter_node(state,   bridge)

    g = StateGraph(AgentState)
    g.add_node("researcher", _researcher)
    g.add_node("validator",  _validator)
    g.add_node("scorer",     _scorer)
    g.add_node("reporter",   _reporter)

    g.set_entry_point("researcher")
    g.add_edge("researcher", "validator")
    g.add_edge("validator",  "scorer")
    g.add_edge("scorer",     "reporter")
    g.add_edge("reporter",   END)

    return g.compile()


# ─────────────────────────────────────────────────────────────────────────────
#  run_pipeline — async generator consumed by FastAPI SSE endpoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_pipeline(
    task:   str,
    run_id: Optional[str] = None,
    bridge: Optional[Any] = None,
) -> AsyncGenerator[dict, None]:
    """
    Yields SSE events as each agent step completes.

    Event types:
      - run_started   : { type, runId, task }
      - step events   : { agentId, action, txHash, step, inputHash, outputHash, trustScore, runId, timestamp }
      - run_complete  : { type, runId, report, score, txCount, txHashes }
      - error         : { type, runId, message }
    """
    if run_id is None:
        run_id = make_run_id()

    logger.info("[Pipeline] Run %s starting — task: %s", run_id, task)

    yield {"type": "run_started", "runId": run_id, "task": task}

    initial_state: AgentState = {
        "task":       task,
        "run_id":     run_id,
        "research":   "",
        "validation": "",
        "score":      0,
        "report":     "",
        "tx_hashes":  [],
        "sse_events": [],
        "messages":   [],
    }

    try:
        graph = build_graph(bridge=bridge)
        prev_event_count = 0

        async for chunk in graph.astream(initial_state):
            for node_name, node_state in chunk.items():
                new_events: list[dict] = node_state.get("sse_events", [])

                # Yield only newly added events from this node
                for evt in new_events[prev_event_count:]:
                    logger.info(
                        "[Pipeline] Event: %s/%s tx=%s",
                        evt.get("agentId"), evt.get("action"),
                        evt.get("txHash", "")[:16]
                    )
                    yield evt

                prev_event_count = len(new_events)

                # Keep tracking latest state
                initial_state = {**initial_state, **node_state}

        final = initial_state
        yield {
            "type":     "run_complete",
            "runId":    run_id,
            "report":   final.get("report", ""),
            "score":    final.get("score", 0),
            "txCount":  len(final.get("tx_hashes", [])),
            "txHashes": final.get("tx_hashes", []),
        }
        logger.info(
            "[Pipeline] Run %s complete — %d txs, score=%d",
            run_id, len(final.get("tx_hashes", [])), final.get("score", 0)
        )

    except Exception as e:
        logger.error("[Pipeline] Error in run %s: %s", run_id, e, exc_info=True)
        yield {"type": "error", "runId": run_id, "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Standalone test — python -m agents.pipeline "your task here"
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s"
    )

    task = sys.argv[1] if len(sys.argv) > 1 else "Research top 3 AI startups in India"

    async def test():
        print(f"\n{'='*60}")
        print(f"Pipeline test: {task}")
        print('='*60 + "\n")

        step_count = 0
        async for event in run_pipeline(task):
            t = event.get("type")

            if t == "run_started":
                print(f"▶  Run ID: {event['runId']}\n")

            elif t == "run_complete":
                print(f"\n{'='*60}")
                print(f"✅  Run complete")
                print(f"    Score:    {event['score']}/100")
                print(f"    Tx count: {event['txCount']}")
                print(f"\n--- Final Report (first 600 chars) ---")
                print(event['report'][:600])

            elif t == "error":
                print(f"\n❌  Error: {event['message']}")

            else:
                step_count += 1
                agent  = event.get('agentId', '?')
                action = event.get('action',  '?')
                tx     = event.get('txHash',  '')[:22]
                score  = event.get('trustScore', 0)
                print(f"  [{step_count:02d}] {agent:12} | {action:25} | score={score:3d} | tx={tx}...")

        print(f"\nTotal steps logged: {step_count}")

    asyncio.run(test())