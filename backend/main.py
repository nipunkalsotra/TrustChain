"""
main.py  —  TrustChain FastAPI backend

Endpoints:
  POST /run-agent          → start pipeline, returns run_id
  GET  /stream/{run_id}    → SSE stream of agent events
  GET  /audit-log          → all on-chain entries from AgentAuditLog
  GET  /trust-scores       → all 4 agent scores for a run
  POST /verify             → hash integrity check against on-chain record

Run:
  uvicorn main:app --reload --port 8000
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from blockchain.client import get_bridge
from agents.pipeline import run_pipeline
from agents.base import make_run_id

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  In-memory run store
#  Holds active + completed runs for the SSE stream to consume
# ─────────────────────────────────────────────────────────────────────────────

# run_id → asyncio.Queue of SSE event dicts
_run_queues: dict[str, asyncio.Queue] = {}

# run_id → final state dict (populated when run_complete fires)
_run_results: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI app + lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: warm up the blockchain bridge so first request is fast."""
    logger.info("TrustChain backend starting up...")
    try:
        bridge = get_bridge()
        logger.info("Blockchain bridge ready — wallet: %s", bridge.account.address)
    except Exception as e:
        logger.error("Bridge init failed: %s", e)
    yield
    logger.info("TrustChain backend shutting down.")


app = FastAPI(
    title="TrustChain API",
    description="Multi-agent AI with every step recorded on Monad testnet",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    task: str
    run_id: Optional[str] = None    # optional — auto-generated if not provided


class RunAgentResponse(BaseModel):
    run_id: str
    task:   str
    status: str = "started"
    stream_url: str


class VerifyRequest(BaseModel):
    agent_id:      str
    code_hash_hex: str              # 0x-prefixed keccak256 of agent source


# ─────────────────────────────────────────────────────────────────────────────
#  Background pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_pipeline_background(task: str, run_id: str):
    """
    Runs the full 4-agent pipeline in the background.
    Puts every SSE event into the run's queue.
    Sentinel value None signals the stream to close.
    """
    queue = _run_queues[run_id]
    bridge = get_bridge()

    try:
        async for event in run_pipeline(task, run_id=run_id, bridge=bridge):
            await queue.put(event)

            # Cache final result so /trust-scores and /audit-log can use it
            if event.get("type") == "run_complete":
                _run_results[run_id] = event

    except Exception as e:
        logger.error("[Pipeline] Background error for %s: %s", run_id, e)
        await queue.put({"type": "error", "runId": run_id, "message": str(e)})

    finally:
        # Sentinel — tells SSE generator to close the stream
        await queue.put(None)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /run-agent
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/run-agent", response_model=RunAgentResponse)
async def run_agent(body: RunAgentRequest):
    """
    Start a new agent pipeline run.
    Returns run_id immediately — stream events via GET /stream/{run_id}
    """
    if not body.task.strip():
        raise HTTPException(status_code=400, detail="task cannot be empty")

    run_id = body.run_id or make_run_id()

    # Create queue for this run
    _run_queues[run_id] = asyncio.Queue()

    # Fire pipeline as background task — don't await
    asyncio.create_task(_run_pipeline_background(body.task, run_id))

    logger.info("[API] Run started: %s — task: %s", run_id, body.task)

    return RunAgentResponse(
        run_id=run_id,
        task=body.task,
        status="started",
        stream_url=f"/stream/{run_id}",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /stream/{run_id}   — SSE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/stream/{run_id}")
async def stream_events(run_id: str):
    """
    SSE endpoint — yields agent events as they happen.

    Each event is:
      data: {"agentId": "researcher", "action": "web_search", "txHash": "0x...", ...}

    Stream closes when run completes or errors.
    """
    # If run_id doesn't exist yet, wait briefly (race condition on fast clients)
    for _ in range(20):
        if run_id in _run_queues:
            break
        await asyncio.sleep(0.1)

    if run_id not in _run_queues:
        raise HTTPException(status_code=404, detail=f"run_id '{run_id}' not found")

    queue = _run_queues[run_id]

    async def event_generator():
        """Pulls from queue and yields SSE-formatted strings."""
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=120)

                # None is the sentinel — pipeline finished
                if event is None:
                    yield "data: [DONE]\n\n"
                    break

                yield f"data: {json.dumps(event)}\n\n"

        except asyncio.TimeoutError:
            logger.warning("[SSE] Stream timeout for run %s", run_id)
            yield "data: [TIMEOUT]\n\n"
        except asyncio.CancelledError:
            logger.info("[SSE] Client disconnected from run %s", run_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",    # nginx: disable buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /audit-log
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/audit-log")
async def get_audit_log(
    run_id: Optional[str] = Query(None, description="Filter by run_id (optional)")
):
    """
    Returns all on-chain entries from AgentAuditLog contract.
    Optionally filter by run_id.
    """
    bridge = get_bridge()
    try:
        entries = await bridge.get_all_audit_entries()
        if run_id:
            # Note: on-chain entries don't store run_id directly,
            # but we can cross-reference via sse_events if needed.
            # For now return all entries — frontend filters by timestamp.
            pass
        return {"entries": entries, "total": len(entries)}
    except Exception as e:
        logger.error("[API] audit-log error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/trust-scores")
async def get_trust_scores(
    run_id: str = Query(..., description="Run ID to fetch scores for")
):
    """
    Returns trust scores for all 4 agents for a specific run.
    Reads from TrustScoreRegistry on Monad.
    """
    bridge = get_bridge()
    try:
        scores = await bridge.get_all_scores(run_id)
        return {"runId": run_id, "scores": scores}
    except Exception as e:
        logger.error("[API] trust-scores error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  POST /verify
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/verify")
async def verify_integrity(body: VerifyRequest):
    """
    Verify an agent's code hash against the on-chain record.
    Used by the 'Verify Integrity' button in the frontend.

    Returns: { agentId, matches, exists, verified }
    """
    bridge = get_bridge()
    try:
        result = await bridge.verify_integrity(body.agent_id, body.code_hash_hex)
        return result
    except Exception as e:
        logger.error("[API] verify error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  GET /health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Quick health check — also verifies blockchain connection."""
    bridge = get_bridge()
    return {
        "status":   "ok",
        "chain_id": bridge.w3.eth.chain_id,
        "wallet":   bridge.account.address,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /runs/{run_id}  — fetch cached result for a completed run
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    """
    Returns the cached result for a completed run.
    Useful for the frontend to fetch the final report after the stream closes.
    """
    if run_id not in _run_results:
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or not yet complete"
        )
    return _run_results[run_id]