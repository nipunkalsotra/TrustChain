"""
blockchain MCP server — port 8002
Exposes one read-only tool to LangGraph agents via FastMCP:
  - verify_integrity(agent_id, code_hash_hex)

Wraps BlockchainBridge from backend/blockchain/client.py.
Run from repo root: python mcp_servers/blockchain/server.py

F16 (Phase 2 plan's fix list): log_action/update_trust_score write tools
were removed. All writes must flow through the transactional outbox
(agents/base.py::log_step -> anchor worker) and the score writer
(blockchain/score_writer.py -> TrustScoreRegistryV2) so durability and
batching can't be bypassed — an agent calling this MCP server directly to
write would skip both. This also retires the argument-ordering defect
class Phase 1 found here: log_action's positional args didn't match
BlockchainBridge's real signature, and nothing caught it until an audit
entry showed the wrong values on-chain. A tool that can't be called can't
have that class of bug. Neither write tool was ever actually invoked by
any agent node (researcher.py/validator.py only ever call search_web/
fact_check by name — see agents/researcher.py, agents/validator.py), so
this is dead-code removal, not a behavior change.
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
