"""
compute_hashes.py
─────────────────
Run this BEFORE deploying (or before re-registering agents) to get
the real codeHash values for each agent.

The hash schema must NEVER change — both this script and your FastAPI
verify endpoint must use the exact same dict structure + sort_keys=True.

Usage:
    python3 scripts/compute_hashes.py

Output:
    Prints 4 env vars — paste them into your .env file, then re-run
    the deploy script so the real hashes land on-chain.
"""

import json
from web3 import Web3


# ─────────────────────────────────────────────────────────────────────────────
# AGENT CONFIG DICTS
# These must exactly match what your FastAPI backend uses when verifying.
# Change model/version/systemPrompt to match your real agents.
# NEVER change the key names — that breaks the hash.
# ─────────────────────────────────────────────────────────────────────────────

AGENTS = [
    {
        "agentId":      "researcher",
        "model":        "gpt-4o",
        "version":      "2024-11-20",
        "systemPrompt": "You are a research agent. Your job is to search the web and find accurate, relevant information about the given topic. Always cite your sources.",
    },
    {
        "agentId":      "validator",
        "model":        "gpt-4o",
        "version":      "2024-11-20",
        "systemPrompt": "You are a validation agent. Your job is to fact-check and verify claims made by the researcher agent. Flag any inaccuracies or unsupported claims.",
    },
    {
        "agentId":      "scorer",
        "model":        "gpt-4o",
        "version":      "2024-11-20",
        "systemPrompt": "You are a scoring agent. Your job is to evaluate the quality and accuracy of the research and assign a trust score between 0 and 100.",
    },
    {
        "agentId":      "reporter",
        "model":        "gpt-4o",
        "version":      "2024-11-20",
        "systemPrompt": "You are a reporting agent. Your job is to synthesise the research, validation, and scores into a final structured report.",
    },
]


def compute_hash(config: dict) -> str:
    """
    Compute keccak256 of the agent config dict.
    sort_keys=True is critical — dict key order must be deterministic.
    """
    serialised = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return Web3.keccak(text=serialised).hex()


def main():
    print("=" * 60)
    print("  TrustChain Agent Hash Computation")
    print("=" * 60)
    print()

    env_lines = []

    for agent in AGENTS:
        agent_id   = agent["agentId"]
        hash_value = compute_hash(agent)
        env_key    = f"{agent_id.upper()}_HASH"

        print(f"Agent:  {agent_id}")
        print(f"Model:  {agent['model']} ({agent['version']})")
        print(f"Hash:   {hash_value}")
        print()

        env_lines.append(f"{env_key}={hash_value}")

    print("=" * 60)
    print("  COPY THESE INTO backend/.env AND contracts/.env")
    print("=" * 60)
    print()
    for line in env_lines:
        print(line)
    print()
    print("=" * 60)
    print("  NEXT STEP")
    print("=" * 60)
    print("Re-run the deploy script with these values set in .env:")
    print("  forge script script/DeployTrustChain.s.sol \\")
    print("    --rpc-url monad \\")
    print("    --private-key $PRIVATE_KEY \\")
    print("    --broadcast -vvvv")
    print()
    print("Or if contracts are already deployed, update registration via Python:")
    print("  python3 scripts/register_agents.py")
    print()


if __name__ == "__main__":
    main()
