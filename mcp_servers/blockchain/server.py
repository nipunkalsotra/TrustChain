"""
blockchain MCP server — port 8002
Exposes three tools to LangGraph agents via FastMCP:
  - log_action(agent_id, action, input_text, output_text, step_index, run_id)
  - update_trust_score(agent_id, run_id, score, reason)
  - verify_integrity(agent_id, code_hash_hex)

Wraps BlockchainBridge from backend/blockchain/client.py.
Run from repo root: python mcp_servers/blockchain/server.py
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

from fastmcp import FastMCP
from dotenv import load_dotenv

# ── Make backend importable ───────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]   # TrustChain/
sys.path.insert(0, str(ROOT / "backend"))

from blockchain.client import get_bridge      # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("blockchain")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine from a sync FastMCP tool."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def log_action(
    agent_id:   str,
    action:     str,
    input_text: str,
    output_text: str,
    step_index: int,
    run_id:     str,
) -> dict:
    """
    Hash input/output and write a tamper-proof audit entry to Monad.
    Call this after every agent step to build the on-chain black box.

    Args:
        agent_id:    Unique agent identifier e.g. 'researcher', 'validator'.
        action:      Short action label e.g. 'web_search', 'summarise'.
        input_text:  Full input passed to the agent step (will be keccak256-hashed).
        output_text: Full output produced by the agent step (will be keccak256-hashed).
        step_index:  0-based index of this step within the run.
        run_id:      Unique identifier for this pipeline run (used for trust score grouping).

    Returns:
        dict with keys:
          - tx_hash:    0x-prefixed transaction hash on Monad
          - agent_id:   echo
          - action:     echo
          - step_index: echo
          - run_id:     echo
          - explorer:   Monad explorer URL for the tx
    """
    logger.info("[log_action] %s/%s step=%d run=%s", agent_id, action, step_index, run_id)

    try:
        bridge   = get_bridge()
        tx_hash  = _run(bridge.log_action(agent_id, action, input_text, output_text, step_index))

        return {
            "tx_hash":    tx_hash,
            "agent_id":   agent_id,
            "action":     action,
            "step_index": step_index,
            "run_id":     run_id,
            "explorer":   f"https://explorer.monad.xyz/tx/{tx_hash}",
        }

    except Exception as e:
        logger.error("[log_action] error: %s", e)
        return {
            "tx_hash":    None,
            "agent_id":   agent_id,
            "action":     action,
            "step_index": step_index,
            "run_id":     run_id,
            "error":      str(e),
        }


@mcp.tool()
def update_trust_score(
    agent_id: str,
    run_id:   str,
    score:    int,
    reason:   str = "step_complete",
) -> dict:
    """
    Write a trust score for an agent in a specific run to TrustScoreRegistry.
    Call this at the end of each agent's contribution to a run.

    Args:
        agent_id: Unique agent identifier e.g. 'researcher', 'validator'.
        run_id:   Unique run identifier matching the one used in log_action.
        score:    Integer 0-100. Higher = more trustworthy for this run.
        reason:   Short label explaining the score e.g. 'all_steps_verified'.

    Returns:
        dict with keys:
          - tx_hash:  0x-prefixed transaction hash on Monad
          - agent_id: echo
          - run_id:   echo
          - score:    echo
          - reason:   echo
          - explorer: Monad explorer URL
    """
    logger.info("[update_trust_score] %s[%s] score=%d reason=%s", agent_id, run_id, score, reason)

    try:
        bridge  = get_bridge()
        tx_hash = _run(bridge.update_score(agent_id, run_id, score, reason))

        return {
            "tx_hash":  tx_hash,
            "agent_id": agent_id,
            "run_id":   run_id,
            "score":    score,
            "reason":   reason,
            "explorer": f"https://explorer.monad.xyz/tx/{tx_hash}",
        }

    except Exception as e:
        logger.error("[update_trust_score] error: %s", e)
        return {
            "tx_hash":  None,
            "agent_id": agent_id,
            "run_id":   run_id,
            "score":    score,
            "reason":   reason,
            "error":    str(e),
        }


@mcp.tool()
def verify_integrity(agent_id: str, code_hash_hex: str) -> dict:
    """
    Check an agent's current code hash against its registered on-chain fingerprint.
    Use this to detect silent agent substitution attacks.

    Args:
        agent_id:      Agent identifier to look up in AgentIdentityRegistry.
        code_hash_hex: 0x-prefixed keccak256 hash of the agent's current source code.

    Returns:
        dict with keys:
          - agent_id:  echo
          - matches:   True if hash matches on-chain record
          - exists:    True if agent is registered at all
          - verified:  True only if both matches and exists are True
          - tampered:  True if agent exists but hash doesn't match (substitution detected)
    """
    logger.info("[verify_integrity] agent=%s hash=%s", agent_id, code_hash_hex[:10] + "...")

    try:
        bridge = get_bridge()
        result = _run(bridge.verify_integrity(agent_id, code_hash_hex))

        result["tampered"] = result["exists"] and not result["matches"]
        return result

    except Exception as e:
        logger.error("[verify_integrity] error: %s", e)
        return {
            "agent_id":  agent_id,
            "matches":   False,
            "exists":    False,
            "verified":  False,
            "tampered":  False,
            "error":     str(e),
        }


if __name__ == "__main__":
    logger.info("Starting blockchain MCP server on port 8002...")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8002)