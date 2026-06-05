"""
blockchain.py  —  TrustChain web3.py bridge
Matches the ACTUAL deployed contracts on Monad testnet.

  - AgentAuditLog          → logAction()
  - TrustScoreRegistry     → updateScore(agentId, runId, score, reason)
  - AgentIdentityRegistry  → registerAgent() / verify()
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────

RPC_URL       = os.getenv("MONAD_RPC_URL", "https://testnet-rpc.monad.xyz")
PRIVATE_KEY   = os.getenv("PRIVATE_KEY", "")
CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"

# ─────────────────────────────────────────────────────────────────────────────
#  ABIs — matched to your actual deployed contracts
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_LOG_ABI = [
    {
        "name": "logAction",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentId",    "type": "string"},
            {"name": "action",     "type": "string"},
            {"name": "inputHash",  "type": "bytes32"},
            {"name": "outputHash", "type": "bytes32"},
            {"name": "stepIndex",  "type": "uint256"},
        ],
        "outputs": [{"name": "entryId", "type": "uint256"}],
    },
    {
        "name": "getEntry",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "entryId", "type": "uint256"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "agentId",    "type": "string"},
                    {"name": "action",     "type": "string"},
                    {"name": "inputHash",  "type": "bytes32"},
                    {"name": "outputHash", "type": "bytes32"},
                    {"name": "timestamp",  "type": "uint256"},
                    {"name": "stepIndex",  "type": "uint256"},
                ],
            }
        ],
    },
    {
        "name": "totalEntries",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "ActionLogged",
        "type": "event",
        "inputs": [
            {"name": "agentId",    "type": "string",  "indexed": True},
            {"name": "action",     "type": "string",  "indexed": False},
            {"name": "inputHash",  "type": "bytes32", "indexed": False},
            {"name": "outputHash", "type": "bytes32", "indexed": False},
            {"name": "timestamp",  "type": "uint256", "indexed": False},
            {"name": "stepIndex",  "type": "uint256", "indexed": False},
            {"name": "entryId",    "type": "uint256", "indexed": False},
        ],
    },
]

# ── Matches YOUR actual deployed TrustScoreRegistry ──────────────────────────
# updateScore(agentId, runId, score, reason)  — runId-based, onlyOwner
TRUST_SCORE_ABI = [
    {
        "name": "updateScore",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentId", "type": "string"},
            {"name": "runId",   "type": "string"},
            {"name": "score",   "type": "uint256"},
            {"name": "reason",  "type": "string"},
        ],
        "outputs": [],
    },
    {
        "name": "getScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "agentId", "type": "string"},
            {"name": "runId",   "type": "string"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getScoreFull",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "agentId", "type": "string"},
            {"name": "runId",   "type": "string"},
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "currentScore",  "type": "uint256"},
                    {"name": "updateCount",   "type": "uint256"},
                    {"name": "lastUpdatedAt", "type": "uint256"},
                    {"name": "hasScore",      "type": "bool"},
                ],
            }
        ],
    },
    {
        "name": "getRunLeaderboard",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "runId", "type": "string"}],
        "outputs": [
            {"name": "agentIds",    "type": "string[]"},
            {"name": "agentScores", "type": "uint256[]"},
        ],
    },
    {
        "name": "getLatestRunId",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
    {
        "name": "ScoreUpdated",
        "type": "event",
        "inputs": [
            {"name": "agentId",   "type": "string",  "indexed": True},
            {"name": "runId",     "type": "string",  "indexed": True},
            {"name": "newScore",  "type": "uint256", "indexed": False},
            {"name": "timestamp", "type": "uint256", "indexed": False},
            {"name": "reason",    "type": "string",  "indexed": False},
        ],
    },
]

IDENTITY_ABI = [
    {
        "name": "registerAgent",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "agentId",  "type": "string"},
            {"name": "codeHash", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "verify",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "agentId",  "type": "string"},
            {"name": "codeHash", "type": "bytes32"},
        ],
        "outputs": [
            {"name": "matches", "type": "bool"},
            {"name": "exists",  "type": "bool"},
        ],
    },
    {
        "name": "getAgent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "string"}],
        "outputs": [
            {"name": "codeHash",   "type": "bytes32"},
            {"name": "registered", "type": "bool"},
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  BlockchainBridge
# ─────────────────────────────────────────────────────────────────────────────

class BlockchainBridge:
    """
    Single entry point for all on-chain calls.

    Key design:
    - All write methods are async (use await)
    - _send() holds a nonce lock so rapid back-to-back calls never collide
    - run_id is threaded through every trust-score call (matches your contract)
    """

    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to RPC: {RPC_URL}")
        logger.info("Connected to Monad testnet. Chain ID: %s", self.w3.eth.chain_id)

        if not PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY not set in .env")
        self.account = self.w3.eth.account.from_key(PRIVATE_KEY)
        logger.info("Deployer wallet: %s", self.account.address)

        addresses = self._load_addresses()

        self.audit_log = self.w3.eth.contract(
            address=Web3.to_checksum_address(addresses["AgentAuditLog"]),
            abi=AUDIT_LOG_ABI,
        )
        self.trust_score = self.w3.eth.contract(
            address=Web3.to_checksum_address(addresses["TrustScoreRegistry"]),
            abi=TRUST_SCORE_ABI,
        )
        self.identity_reg = self.w3.eth.contract(
            address=Web3.to_checksum_address(addresses["AgentIdentityRegistry"]),
            abi=IDENTITY_ABI,
        )

        logger.info("All 3 contracts loaded ✓")

        # Nonce management — created lazily inside running event loop
        self._nonce_lock: Optional[asyncio.Lock] = None
        self._pending_nonce: Optional[int] = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_addresses(self) -> dict:
        path = CONTRACTS_DIR / "addresses.json"
        if not path.exists():
            raise FileNotFoundError(f"addresses.json not found at {path}")
        with open(path) as f:
            return json.load(f)

    def _keccak(self, text: str) -> bytes:
        """keccak256(string) → bytes32"""
        return Web3.solidity_keccak(["string"], [text])

    def _build_and_send(self, fn, nonce: int) -> str:
        """Sign and broadcast a transaction. Returns 0x-prefixed tx hash."""
        tx = fn.build_transaction({
            "from":     self.account.address,
            "nonce":    nonce,
            "gasPrice": self.w3.eth.gas_price,
            "gas":      500_000,
        })
        signed  = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info("tx sent: %s", tx_hash.hex())
        return "0x" + tx_hash.hex()

    async def _send(self, fn) -> str:
        """
        Async nonce-safe sender.
        Acquires lock → grabs/increments nonce → fires tx.
        Multiple agents can call this concurrently without collisions.
        """
        if self._nonce_lock is None:
            self._nonce_lock = asyncio.Lock()

        async with self._nonce_lock:
            on_chain = await asyncio.to_thread(
                self.w3.eth.get_transaction_count, self.account.address
            )
            if self._pending_nonce is None or on_chain > self._pending_nonce:
                self._pending_nonce = on_chain
            nonce = self._pending_nonce
            self._pending_nonce += 1

        return await asyncio.to_thread(self._build_and_send, fn, nonce)

    def _wait(self, tx_hash_hex: str, timeout: int = 30) -> dict:
        """Block until tx is confirmed. Returns receipt dict."""
        receipt = self.w3.eth.wait_for_transaction_receipt(
            bytes.fromhex(tx_hash_hex[2:]), timeout=timeout
        )
        return dict(receipt)

    # ── AgentAuditLog ─────────────────────────────────────────────────────────

    async def log_action(
        self,
        agent_id: str,
        action: str,
        input_text: str,
        output_text: str,
        step_index: int,
    ) -> str:
        """
        Hash input/output and write an audit step on-chain.
        Called after every agent step. Returns tx hash immediately.
        """
        fn = self.audit_log.functions.logAction(
            agent_id,
            action,
            self._keccak(input_text),
            self._keccak(output_text),
            step_index,
        )
        tx_hash = await self._send(fn)
        logger.info("[AuditLog] %s/%s step=%d → %s", agent_id, action, step_index, tx_hash)
        return tx_hash

    async def get_all_audit_entries(self) -> list[dict]:
        """Fetch all entries for GET /audit-log endpoint."""
        total = await asyncio.to_thread(
            self.audit_log.functions.totalEntries().call
        )
        entries = []
        for i in range(total):
            raw = await asyncio.to_thread(
                self.audit_log.functions.getEntry(i).call
            )
            entries.append({
                "entryId":    i,
                "agentId":    raw[0],
                "action":     raw[1],
                "inputHash":  "0x" + raw[2].hex(),
                "outputHash": "0x" + raw[3].hex(),
                "timestamp":  raw[4],
                "stepIndex":  raw[5],
            })
        return entries

    # ── TrustScoreRegistry ────────────────────────────────────────────────────

    async def update_score(
        self,
        agent_id: str,
        run_id: str,
        score: int,
        reason: str = "step_complete",
    ) -> str:
        """
        Write a trust score for an agent in a specific run.
        score must be 0-100. reason is a short label e.g. 'web_search_complete'.
        """
        assert 0 <= score <= 100, "score must be 0-100"
        fn = self.trust_score.functions.updateScore(agent_id, run_id, score, reason)
        tx_hash = await self._send(fn)
        logger.info("[TrustScore] %s[%s] = %d (%s) → %s", agent_id, run_id, score, reason, tx_hash)
        return tx_hash

    async def get_score(self, agent_id: str, run_id: str) -> dict:
        """Read one agent's score for a run."""
        score = await asyncio.to_thread(
            self.trust_score.functions.getScore(agent_id, run_id).call
        )
        return {"agentId": agent_id, "runId": run_id, "score": score}

    async def get_all_scores(self, run_id: str) -> list[dict]:
        """
        Read all 4 agent scores for GET /trust-scores.
        Uses getRunLeaderboard for efficiency (1 call instead of 4).
        """
        try:
            agent_ids, agent_scores = await asyncio.to_thread(
                self.trust_score.functions.getRunLeaderboard(run_id).call
            )
            return [
                {"agentId": aid, "runId": run_id, "score": s}
                for aid, s in zip(agent_ids, agent_scores)
            ]
        except Exception:
            # Fallback: run hasn't started yet, return zeros
            agents = ["researcher", "validator", "scorer", "reporter"]
            return [{"agentId": a, "runId": run_id, "score": 0} for a in agents]

    async def get_latest_run_id(self) -> str:
        """Get the most recent runId from the contract."""
        return await asyncio.to_thread(
            self.trust_score.functions.getLatestRunId().call
        )

    # ── AgentIdentityRegistry ─────────────────────────────────────────────────

    async def register_agent(self, agent_id: str, code_hash_hex: str) -> str:
        """Register an agent's code hash. code_hash_hex is 0x-prefixed."""
        code_hash = bytes.fromhex(code_hash_hex[2:])
        fn = self.identity_reg.functions.registerAgent(agent_id, code_hash)
        tx_hash = await self._send(fn)
        logger.info("[Identity] registered %s → %s", agent_id, tx_hash)
        return tx_hash

    async def verify_integrity(self, agent_id: str, code_hash_hex: str) -> dict:
        """
        Check an agent's hash against on-chain record.
        Used by POST /verify endpoint.
        """
        code_hash = bytes.fromhex(code_hash_hex[2:])
        matches, exists = await asyncio.to_thread(
            self.identity_reg.functions.verify(agent_id, code_hash).call
        )
        return {
            "agentId":  agent_id,
            "matches":  matches,
            "exists":   exists,
            "verified": matches and exists,
        }

    # ── Event listener (background task for SSE) ──────────────────────────────

    async def listen_action_logged(self, queue: asyncio.Queue):
        """
        Polls ActionLogged events and puts SSE-ready dicts into the queue.
        Start as a background task in FastAPI lifespan:
            asyncio.create_task(bridge.listen_action_logged(sse_queue))
        """
        event_filter = await asyncio.to_thread(
            self.audit_log.events.ActionLogged.create_filter,
            fromBlock="latest",
        )
        logger.info("ActionLogged event listener started")

        while True:
            try:
                new_events = await asyncio.to_thread(event_filter.get_new_entries)
                for evt in new_events:
                    args = evt["args"]
                    payload = {
                        "agentId":    args["agentId"],
                        "action":     args["action"],
                        "txHash":     "0x" + evt["transactionHash"].hex(),
                        "step":       args["stepIndex"],
                        "inputHash":  "0x" + args["inputHash"].hex(),
                        "outputHash": "0x" + args["outputHash"].hex(),
                        "entryId":    args["entryId"],
                        "timestamp":  args["timestamp"],
                        "trustScore": None,   # filled in by SSE handler
                    }
                    await queue.put(payload)
                    logger.info("Event queued: %s", payload["txHash"])
            except Exception as e:
                logger.warning("Event listener error: %s", e)

            await asyncio.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton — import this everywhere
# ─────────────────────────────────────────────────────────────────────────────

_bridge: Optional[BlockchainBridge] = None

def get_bridge() -> BlockchainBridge:
    global _bridge
    if _bridge is None:
        _bridge = BlockchainBridge()
    return _bridge


# ─────────────────────────────────────────────────────────────────────────────
#  Smoke test — python blockchain.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)

    async def smoke_test():
        print("\n=== TrustChain blockchain.py smoke test ===\n")
        bridge = BlockchainBridge()

        TEST_RUN_ID = "smoke_test_run_001"

        # 1. Log a test action
        print("1. Logging a test action...")
        tx = await bridge.log_action("researcher", "smoke_test", "test input", "test output", 0)
        print(f"   ✓ tx: {tx}")
        print(f"   🔍 https://explorer.monad.xyz/tx/{tx}")
        receipt = await asyncio.to_thread(bridge._wait, tx)
        print(f"   ✓ confirmed in block {receipt['blockNumber']}\n")

        # 2. Update trust score (with runId + reason)
        print("2. Updating trust score for researcher...")
        tx2 = await bridge.update_score("researcher", TEST_RUN_ID, 85, "smoke_test_complete")
        print(f"   ✓ tx: {tx2}")
        receipt2 = await asyncio.to_thread(bridge._wait, tx2)
        print(f"   ✓ confirmed in block {receipt2['blockNumber']}\n")

        # 3. Read the score back
        print("3. Reading trust score...")
        score_data = await bridge.get_score("researcher", TEST_RUN_ID)
        print(f"   ✓ {score_data}\n")

        # 4. Read leaderboard
        print("4. Run leaderboard:")
        scores = await bridge.get_all_scores(TEST_RUN_ID)
        for s in scores:
            print(f"   {s['agentId']:12} score={s['score']}")

        print("\n=== Smoke test complete ✓ ===")
        print(f"Explorer: https://explorer.monad.xyz/address/{bridge.account.address}")

    asyncio.run(smoke_test())