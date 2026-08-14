"""
blockchain/hashing_utils.py
────────────────────────────
Run this BEFORE deploying (or before re-registering agents) to get
the real codeHash values for each agent. Also imported directly by
blockchain/client.py (AGENTS, compute_hash) to recompute live hashes
during /verify.

The hash schema must NEVER change — both this script and client.py's
verify_run() must use the exact same dict structure + sort_keys=True.

Usage:
    cd backend
    python3 blockchain/hashing_utils.py

Output:
    Prints 4 env vars for contracts/.env (used by the Foundry deploy
    script), and the next steps to push them on-chain — either via a
    fresh `forge script` deploy, or via scripts/register_agents.py if
    the contracts are already deployed.
"""

import json
from web3 import Web3

from agents.llm_provider import MODEL_NAME, MODEL_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# AGENT CONFIG DICTS
# These must exactly match what your FastAPI backend uses when verifying.
# Change model/version/systemPrompt to match your real agents.
# NEVER change the key names — that breaks the hash.
#
# `systemPrompt` is copied verbatim from the SystemMessage each agent node
# actually sends to the LLM (agents/researcher.py, validator.py, scorer.py,
# reporter.py). Keep these two places in sync — if you edit a prompt in one
# of those files, update it here too and re-run this script + re-register
# (see backend/scripts/register_agents.py), or the identity hash will stop
# reflecting the agent that's actually running.
#
# reporter's runtime prompt interpolates the live `score` value via an
# f-string, so no single literal string equals what's sent on every run.
# The identity hash instead fingerprints the fixed *template* (with "{score}"
# left as a literal placeholder) — that's the part of reporter's behavior
# that should stay constant regardless of any one run's score.
# ─────────────────────────────────────────────────────────────────────────────

AGENTS = [
    {
        "agentId":      "researcher",
        "model":        MODEL_NAME,
        "version":      MODEL_VERSION,
        "systemPrompt": (
            "You are the Researcher agent in a multi-agent AI system called TrustChain. "
            "Your job is to synthesise web search results into clear, factual research findings. "
            "Be concise, structured, and cite sources where possible. "
            "Output 3-5 key findings as a numbered list."
        ),
    },
    {
        "agentId":      "validator",
        "model":        MODEL_NAME,
        "version":      MODEL_VERSION,
        "systemPrompt": (
            "You are the Validator agent in TrustChain. "
            "Compare the original research against fact-check results. "
            "Identify: (1) CONFIRMED claims, (2) UNCERTAIN claims, (3) ERRORS or hallucinations.\n\n"
            "End with a single line:\n"
            "VERDICT: PASS | PARTIAL | FAIL — <one sentence reason>"
        ),
    },
    {
        "agentId":      "scorer",
        "model":        MODEL_NAME,
        "version":      MODEL_VERSION,
        "systemPrompt": (
            "You are the Scorer agent in TrustChain. "
            "Assign trust scores (0-100) for all 4 agents based on pipeline quality.\n\n"
            "Scoring rules:\n"
            "- researcher: depth, relevance, source quality of research\n"
            "- validator:  thoroughness of fact-checking, verdict clarity\n"
            "- scorer:     give yourself 80 (you are running correctly)\n"
            "- reporter:   pre-score 75 (will produce final report)\n\n"
            "IMPORTANT — output EXACTLY this format:\n"
            "researcher SCORE: <0-100>\n"
            "validator SCORE: <0-100>\n"
            "scorer SCORE: <0-100>\n"
            "reporter SCORE: <0-100>\n"
            "Reasoning: <one sentence>"
        ),
    },
    {
        "agentId":      "reporter",
        "model":        MODEL_NAME,
        "version":      MODEL_VERSION,
        "systemPrompt": (
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
            "Pipeline trust score: {score}/100\n"
            "<one sentence on what this score means>\n\n"
            "## Blockchain Audit\n"
            "All agent steps are permanently recorded on Monad testnet. "
            "Every action is immutably logged and verifiable via transaction hash."
        ),
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
