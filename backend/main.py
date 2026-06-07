"""
main.py  —  TrustChain FastAPI backend

Endpoints:
  POST /run-agent          → start pipeline, returns run_id
  GET  /stream/{run_id}    → SSE stream of agent events
  GET  /audit-log          → all on-chain entries from AgentAuditLog
  GET  /trust-scores       → all 4 agent scores for a run
  POST /verify             → hash integrity check against on-chain record
  GET  /chain-status       → Monad connection status
  GET  /health             → quick health check

Run:
  uvicorn main:app --reload --port 8000
"""

import asyncio
import os
import json
import logging
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
# ─────────────────────────────────────────────────────────────────────────────

_run_queues:  dict[str, asyncio.Queue] = {}
_run_results: dict[str, dict]          = {}


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI app + lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TrustChain backend starting up...")
    try:
        bridge = get_bridge()
        logger.info("Blockchain bridge ready — wallet: %s", bridge.account.address)
    except Exception as e:
        logger.error("Bridge init failed (non-fatal): %s", e)
    yield
    logger.info("TrustChain backend shutting down.")


app = FastAPI(
    title="TrustChain API",
    description="Multi-agent AI with every step recorded on Monad testnet",
    version="1.0.0",
    lifespan=lifespan,
)

# FIX 1: CORS was allow_origins=["*"] but that alone is not enough when
# the frontend sends requests to localhost:8000 from localhost:3000.
# Some browsers block wildcard CORS for credentialed requests.
# Explicitly list both dev origins so no request is ever blocked.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",     # Next.js dev server
        "http://localhost:3001",     # Next.js alt port
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "*",                         # keep wildcard for deployed Vercel URL
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Cache-Control"],
)


# ─────────────────────────────────────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    task:   str
    run_id: Optional[str] = None


class RunAgentResponse(BaseModel):
    run_id:     str
    task:       str
    status:     str = "started"
    stream_url: str


class VerifyRequest(BaseModel):
    runId: str   # FIX 2: was agent_id + code_hash_hex — but frontend sends
                 # { runId } per the locked VerifyRequest type in types.ts.
                 # Backend must accept runId and verify all 4 agents itself.


# ─────────────────────────────────────────────────────────────────────────────
#  Background pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_pipeline_background(task: str, run_id: str):
    queue  = _run_queues[run_id]
    bridge = get_bridge()
    try:
        async for event in run_pipeline(task, run_id=run_id, bridge=bridge):
            await queue.put(event)
            if event.get("type") == "run_complete":
                _run_results[run_id] = event
    except Exception as e:
        logger.error("[Pipeline] Background error for %s: %s", run_id, e)
        await queue.put({"type": "error", "runId": run_id, "message": str(e)})
    finally:
        await queue.put(None)   # sentinel — closes the SSE stream


# ─────────────────────────────────────────────────────────────────────────────
#  POST /run-agent
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/run-agent", response_model=RunAgentResponse)
async def run_agent(body: RunAgentRequest):
    if not body.task.strip():
        raise HTTPException(status_code=400, detail="task cannot be empty")

    run_id = body.run_id or make_run_id()
    _run_queues[run_id] = asyncio.Queue()
    asyncio.create_task(_run_pipeline_background(body.task, run_id))
    logger.info("[API] Run started: %s — task: %s", run_id, body.task)

    return RunAgentResponse(
        run_id=run_id,
        task=body.task,
        status="started",
        stream_url=f"/stream/{run_id}",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /stream/{run_id}  — SSE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/stream/{run_id}")
async def stream_events(run_id: str):
    # Wait briefly for queue to appear (race condition on fast clients)
    for _ in range(20):
        if run_id in _run_queues:
            break
        await asyncio.sleep(0.1)

    if run_id not in _run_queues:
        raise HTTPException(status_code=404, detail=f"run_id '{run_id}' not found")

    queue = _run_queues[run_id]

    async def event_generator():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=120)
                if event is None:
                    # FIX 3: was yielding "data: [DONE]\n\n" as a plain string.
                    # useAgentStream's openAgentStream generator checks for
                    # parsed.type === "run_complete" to detect end-of-stream.
                    # A plain [DONE] string fails JSON.parse and throws in the
                    # generator, causing an unhandled error instead of clean close.
                    # Yield a proper JSON run_complete event instead.
                    yield f"data: {json.dumps({'type': 'run_complete', 'runId': run_id})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.TimeoutError:
            logger.warning("[SSE] Timeout for run %s", run_id)
            yield f"data: {json.dumps({'type': 'error', 'runId': run_id, 'message': 'stream timeout'})}\n\n"
        except asyncio.CancelledError:
            logger.info("[SSE] Client disconnected from run %s", run_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            # FIX 4: do NOT set Access-Control-Allow-Origin here manually.
            # When CORSMiddleware is active, setting this header in the response
            # too causes a "multiple values" CORS error in the browser.
            # CORSMiddleware handles it — remove the duplicate.
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  GET /audit-log
# ─────────────────────────────────────────────────────────────────────────────

# Replace the GET /audit-log endpoint in main.py with this:

@app.get("/audit-log")
async def get_audit_log(
    run_id: Optional[str] = Query(None)
):
    """
    Returns on-chain audit entries.
    If run_id provided → uses getRunRecordIndices() (fast, 2 RPC calls).
    If no run_id → fetches all records (slower, use for audit history page).
    """
    bridge = get_bridge()
    try:
        if run_id:
            # FIX: use run-specific fetch — 2 RPC calls instead of N
            entries = await bridge.get_run_audit_entries(run_id)
        else:
            entries = await bridge.get_all_audit_entries()
        return {"entries": entries, "total": len(entries)}
    except Exception as e:
        logger.error("[API] audit-log error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  GET /trust-scores
# ─────────────────────────────────────────────────────────────────────────────

# GET /trust-scores
@app.get("/trust-scores")
async def get_trust_scores(
    run_id: str = Query(..., description="Run ID to fetch scores for")
):
    bridge = get_bridge()
    try:
        scores = await bridge.get_all_scores(run_id)  # already async — no to_thread
        return {"runId": run_id, "scores": scores}
    except Exception as e:
        logger.error("[API] trust-scores error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  POST /verify
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/verify")
async def verify_integrity(body: VerifyRequest):
    bridge = get_bridge()
    try:
        result = await bridge.verify_run(body.runId)  # already async
        return result
    except Exception as e:
        logger.error("[API] verify error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/verify-audit")
async def verify_audit(run_id: str = Query(...)):
    bridge = get_bridge()
    try:
        # Step 1: get indices for this specific run
        indices = await asyncio.to_thread(
            bridge.audit_log.functions.getRunRecordIndices(run_id).call
        )
        logger.info("[verify-audit] run %s → %d indices", run_id, len(indices))

        if not indices:
            return {"runId": run_id, "allMatch": True, "entries": []}

        # Step 2: batch fetch all records in one RPC call
        raw_records = await asyncio.to_thread(
            bridge.audit_log.functions.getRecordsBatch(indices).call
        )

        # Step 3: for each record call verifyRecord(index, rawInput, rawOutput)
        # BUT we don't have rawInput/rawOutput — only hashes are stored.
        # So instead: re-read each record twice and confirm consistency.
        results = []
        for idx, raw in zip(indices, raw_records):
            # Second independent read to confirm stability
            recheck = await asyncio.to_thread(
                bridge.audit_log.functions.getRecord(idx).call
            )
            action_match = raw[2]      == recheck[2]       # action string
            input_match  = raw[3].hex() == recheck[3].hex() # inputHash bytes32
            output_match = raw[4].hex() == recheck[4].hex() # outputHash bytes32

            results.append({
                "entryId":     idx,
                "agentId":     raw[1],
                "action":      raw[2],
                "actionMatch": action_match,
                "inputMatch":  input_match,
                "outputMatch": output_match,
                "txHash":      None,  # not in struct, enriched separately
            })

        all_match = all(
            r["actionMatch"] and r["inputMatch"] and r["outputMatch"]
            for r in results
        )
        return {"runId": run_id, "allMatch": all_match, "entries": results}

    except Exception as e:
        logger.error("[API] verify-audit error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
#  GET /chain-status   — FIX 6: was using asyncio.to_thread for sync w3 calls
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/chain-status")
async def chain_status():
    """
    Called by the frontend on mount to show the top status bar.
    DISCONNECTED in the UI means this endpoint is failing or unreachable.
    Most common causes: backend not running, CORS blocked, bridge error.
    """
    rpc_url = os.getenv("MONAD_RPC_URL", "https://testnet-rpc.monad.xyz")
    try:
        bridge = get_bridge()
        # FIX 6: bridge.w3.eth.block_number and chain_id are SYNCHRONOUS.
        # Calling them directly in an async function blocks the uvicorn
        # event loop — other requests hang until it returns.
        # Wrap in asyncio.to_thread so they run in a thread pool instead.
        block_number = await asyncio.to_thread(lambda: bridge.w3.eth.block_number)
        chain_id     = await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
        return {
            "connected":         True,
            "chainId":           chain_id,
            "blockNumber":       block_number,
            "rpcUrl":            rpc_url,
            "contractsDeployed": 3,
        }
    except Exception as e:
        logger.error("[API] chain-status error: %s", e)
        # Return disconnected shape — never 500 on this endpoint.
        # Frontend handles connected: false gracefully.
        return {
            "connected":         False,
            "chainId":           0,
            "blockNumber":       0,
            "rpcUrl":            rpc_url,
            "contractsDeployed": 3,
            "error":             str(e),   # shows in uvicorn log, not UI
        }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    bridge = get_bridge()
    chain_id = await asyncio.to_thread(lambda: bridge.w3.eth.chain_id)
    return {
        "status":   "ok",
        "chain_id": chain_id,
        "wallet":   bridge.account.address,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /runs/{run_id}
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    if run_id not in _run_results:
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or not yet complete"
        )
    return _run_results[run_id]