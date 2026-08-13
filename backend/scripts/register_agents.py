"""
scripts/register_agents.py
───────────────────────────
Push the agent identity hashes from blockchain/hashing_utils.py's AGENTS
list to the already-deployed AgentIdentityRegistry contract, without
needing to redeploy.

registerAgent() is idempotent per agentId — if the agent is already
registered, it just updates codeHash/modelName/modelVersion in place
(see contracts/src/AgentIdentityRegistry.sol: emits AgentUpdated instead
of AgentRegistered on a second call for the same agentId).

Run this manually after editing hashing_utils.py's AGENTS list (e.g. after
changing a system prompt or the LLM model/version). It performs REAL
on-chain transactions signed by PRIVATE_KEY and spends real (testnet) gas
— it is not part of the app's normal startup path.

Usage:
    cd backend
    python3 scripts/register_agents.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blockchain.client import get_bridge          # noqa: E402
from blockchain.hashing_utils import AGENTS, compute_hash  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    bridge = get_bridge()

    print("=" * 60)
    print("  Registering/updating agent identities on-chain")
    print("=" * 60)

    for agent in AGENTS:
        agent_id  = agent["agentId"]
        code_hash = compute_hash(agent)
        print(f"\n{agent_id}")
        print(f"  model:      {agent['model']} ({agent['version']})")
        print(f"  codeHash:   {code_hash}")

        tx_hash = await bridge.register_agent(
            agent_id=agent_id,
            code_hash_hex=code_hash,
            model_name=agent["model"],
            model_version=agent["version"],
        )
        print(f"  tx:         {tx_hash}")

    print("\nDone. Verify with: GET /chain-status then POST /verify {'runId': '<any_run_id>'}.")


if __name__ == "__main__":
    asyncio.run(main())
