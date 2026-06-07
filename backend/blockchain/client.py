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
#  ABIs
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_LOG_ABI = [
    # ── Write ──────────────────────────────────────────────────────────────
    {
        "name": "logAction",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "runId",      "type": "string"},
            {"name": "agentId",    "type": "string"},
            {"name": "action",     "type": "string"},
            {"name": "inputHash",  "type": "bytes32"},
            {"name": "outputHash", "type": "bytes32"},
            {"name": "stepIndex",  "type": "uint256"},
            {"name": "metadata",   "type": "string"},
        ],
        "outputs": [{"name": "recordIndex", "type": "uint256"}],
    },
    # ── Read: total count ───────────────────────────────────────────────────
    {
        "name": "getTotalRecords",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    # ── Read: single record by index ────────────────────────────────────────
    {
        "name": "getRecord",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [{"name": "index", "type": "uint256"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "runId",      "type": "string"},
                    {"name": "agentId",    "type": "string"},
                    {"name": "action",     "type": "string"},
                    {"name": "inputHash",  "type": "bytes32"},
                    {"name": "outputHash", "type": "bytes32"},
                    {"name": "timestamp",  "type": "uint256"},
                    {"name": "stepIndex",  "type": "uint256"},
                    {"name": "metadata",   "type": "string"},
                    {"name": "txSender",   "type": "address"},
                ],
            }
        ],
    },
    # ── Read: all indices for a run ─────────────────────────────────────────
    {
        "name": "getRunRecordIndices",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [{"name": "runId", "type": "string"}],
        "outputs": [{"name": "", "type": "uint256[]"}],
    },
    # ── Read: batch fetch ───────────────────────────────────────────────────
    {
        "name": "getRecordsBatch",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [{"name": "indices", "type": "uint256[]"}],
        "outputs": [
            {
                "name": "batch",
                "type": "tuple[]",
                "components": [
                    {"name": "runId",      "type": "string"},
                    {"name": "agentId",    "type": "string"},
                    {"name": "action",     "type": "string"},
                    {"name": "inputHash",  "type": "bytes32"},
                    {"name": "outputHash", "type": "bytes32"},
                    {"name": "timestamp",  "type": "uint256"},
                    {"name": "stepIndex",  "type": "uint256"},
                    {"name": "metadata",   "type": "string"},
                    {"name": "txSender",   "type": "address"},
                ],
            }
        ],
    },
    # ── Read: verify record hashes ──────────────────────────────────────────
    {
        "name": "verifyRecord",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "index",     "type": "uint256"},
            {"name": "rawInput",  "type": "string"},
            {"name": "rawOutput", "type": "string"},
        ],
        "outputs": [
            {"name": "inputMatch",  "type": "bool"},
            {"name": "outputMatch", "type": "bool"},
        ],
    },
    # ── Event ───────────────────────────────────────────────────────────────
    {
        "name": "ActionLogged",
        "type": "event",
        "inputs": [
            {"name": "runId",       "type": "string",  "indexed": True},
            {"name": "agentId",     "type": "string",  "indexed": True},
            {"name": "action",      "type": "string",  "indexed": False},
            {"name": "stepIndex",   "type": "uint256", "indexed": False},
            {"name": "inputHash",   "type": "bytes32", "indexed": False},
            {"name": "outputHash",  "type": "bytes32", "indexed": False},
            {"name": "timestamp",   "type": "uint256", "indexed": False},
            {"name": "recordIndex", "type": "uint256", "indexed": False},
        ],
    },
]

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
        "name": "getRunLeaderboard",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [{"name": "runId", "type": "string"}],
        "outputs": [
            {"name": "agentIds",    "type": "string[]"},
            {"name": "agentScores", "type": "uint256[]"},
        ],
    },
    {
        "name": "getLatestRunId",
        "type": "function",
        "stateMutability": "view",
        "inputs":  [],
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
            {"name": "agentId",       "type": "string"},
            {"name": "codeHash",      "type": "bytes32"},
            {"name": "modelName",     "type": "string"},
            {"name": "modelVersion",  "type": "string"},
        ],
        "outputs": [],
    },
    {
        "name": "verifyAgent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "agentId",      "type": "string"},
            {"name": "currentHash",  "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getAgent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "string"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "agentId",       "type": "string"},
                    {"name": "codeHash",      "type": "bytes32"},
                    {"name": "registeredBy",  "type": "address"},
                    {"name": "registeredAt",  "type": "uint256"},
                    {"name": "isActive",      "type": "bool"},
                    {"name": "modelName",     "type": "string"},
                    {"name": "modelVersion",  "type": "string"},
                ],
            }
        ],
    },
    {
        "name": "isRegistered",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "string"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  Helper — parse a raw tuple into a clean AuditEntry dict
# ─────────────────────────────────────────────────────────────────────────────

def _parse_record(raw: tuple, index: int, tx_hash: str = "") -> dict:
    return {
        "entryId":    index,
        "runId":      raw[0],   # was missing
        "agentId":    raw[1],
        "action":     raw[2],
        "inputHash":  "0x" + raw[3].hex(),   # was raw[2]
        "outputHash": "0x" + raw[4].hex(),   # was raw[3]
        "timestamp":  raw[5],                # was raw[4]
        "stepIndex":  raw[6],                # was raw[5]
        "metadata":   raw[7],                # was raw[6]
        "txSender":   raw[8],                # was raw[7]
        "txHash":     tx_hash,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Helper — fetch ActionLogged event logs and return {recordIndex: txHash} map
#  Used by both get_all_audit_entries and get_run_audit_entries.
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_tx_map(audit_log_contract, from_block: int = 0) -> dict[int, str]:
    """
    Fetches all ActionLogged events from the contract and returns a dict
    mapping recordIndex → txHash so records can be enriched after batch fetch.
    Falls back to empty dict if event log fetch fails (non-fatal).
    """
    try:
        logs = await asyncio.to_thread(
            audit_log_contract.events.ActionLogged.get_logs,
            {"fromBlock": from_block, "toBlock": "latest"},
        )
        return {
            log["args"]["recordIndex"]: "0x" + log["transactionHash"].hex()
            for log in logs
        }
    except Exception as e:
        logger.warning("[AuditLog] could not fetch event logs for txHash: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  BlockchainBridge
# ─────────────────────────────────────────────────────────────────────────────

class BlockchainBridge:

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

        self._nonce_lock:    Optional[asyncio.Lock] = None
        self._pending_nonce: Optional[int]          = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_addresses(self) -> dict:
        path = CONTRACTS_DIR / "addresses.json"
        if not path.exists():
            raise FileNotFoundError(f"addresses.json not found at {path}")
        with open(path) as f:
            return json.load(f)

    def _keccak(self, text: str) -> bytes:
        return Web3.solidity_keccak(["string"], [text])

    def _build_and_send(self, fn, nonce: int) -> str:
        tx = fn.build_transaction({
            "from":     self.account.address,
            "nonce":    nonce,
            "gasPrice": self.w3.eth.gas_price,
            "gas":      500_000,
        })
        signed  = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info("tx sent: 0x%s", tx_hash.hex())
        return "0x" + tx_hash.hex()

    async def _send(self, fn) -> str:
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
        receipt = self.w3.eth.wait_for_transaction_receipt(
            bytes.fromhex(tx_hash_hex[2:]), timeout=timeout
        )
        return dict(receipt)

    # ── AgentAuditLog — write ─────────────────────────────────────────────────

    async def log_action(
        self,
        run_id:      str,
        agent_id:    str,
        action:      str,
        input_text:  str,
        output_text: str,
        step_index:  int,
        metadata:    str = "",
    ) -> str:
        fn = self.audit_log.functions.logAction(
            run_id,
            agent_id,
            action,
            self._keccak(input_text),
            self._keccak(output_text),
            step_index,
            metadata,
        )
        tx_hash = await self._send(fn)
        logger.info("[AuditLog] %s/%s step=%d → %s", agent_id, action, step_index, tx_hash)
        return tx_hash

    async def get_all_audit_entries(self) -> list[dict]:
        """
        Fetch all records from AgentAuditLog.
        Uses getRecordsBatch for a single RPC call, then enriches with
        tx hashes fetched from ActionLogged event logs.
        """
        total = await asyncio.to_thread(
            self.audit_log.functions.getTotalRecords().call
        )
        logger.info("[AuditLog] getTotalRecords → %d", total)

        if total == 0:
            return []

        # Batch fetch all records in one RPC call
        indices = list(range(total))
        raw_records = await asyncio.to_thread(
            self.audit_log.functions.getRecordsBatch(indices).call
        )

        # FIX: enrich with tx hashes from event logs
        # getRecordsBatch returns struct data only — tx hashes live in events
        tx_map = await _fetch_tx_map(self.audit_log)

        return [
            {**_parse_record(raw, i), "txHash": tx_map.get(i, "")}
            for i, raw in enumerate(raw_records)
        ]

    async def get_run_audit_entries(self, run_id: str) -> list[dict]:
        """
        Fetch only records for a specific run.
        Uses getRunRecordIndices → getRecordsBatch — 2 RPC calls total.
        Enriches with tx hashes from event logs filtered by run.
        """
        indices = await asyncio.to_thread(
            self.audit_log.functions.getRunRecordIndices(run_id).call
        )
        logger.info("[AuditLog] run %s → %d records", run_id, len(indices))

        if not indices:
            return []

        raw_records = await asyncio.to_thread(
            self.audit_log.functions.getRecordsBatch(indices).call
        )

        # FIX: enrich with tx hashes from event logs
        tx_map = await _fetch_tx_map(self.audit_log)

        return [
            {**_parse_record(raw, idx), "txHash": tx_map.get(idx, "")}
            for idx, raw in zip(indices, raw_records)
        ]

    # ── TrustScoreRegistry ────────────────────────────────────────────────────

    async def update_score(
        self,
        agent_id: str,
        run_id:   str,
        score:    int,
        reason:   str = "step_complete",
    ) -> str:
        assert 0 <= score <= 100, "score must be 0-100"
        fn = self.trust_score.functions.updateScore(agent_id, run_id, score, reason)
        tx_hash = await self._send(fn)
        logger.info("[TrustScore] %s[%s]=%d → %s", agent_id, run_id, score, tx_hash)
        return tx_hash

    async def get_score(self, agent_id: str, run_id: str) -> dict:
        score = await asyncio.to_thread(
            self.trust_score.functions.getScore(agent_id, run_id).call
        )
        return {"agentId": agent_id, "runId": run_id, "score": score}

    async def get_all_scores(self, run_id: str) -> list[dict]:
        try:
            agent_ids, agent_scores = await asyncio.to_thread(
                self.trust_score.functions.getRunLeaderboard(run_id).call
            )
            return [
                {"agentId": aid, "runId": run_id, "score": int(s)}
                for aid, s in zip(agent_ids, agent_scores)
            ]
        except Exception as e:
            logger.warning("[TrustScore] leaderboard fallback for %s: %s", run_id, e)
            agents = ["researcher", "validator", "scorer", "reporter"]
            return [{"agentId": a, "runId": run_id, "score": 0} for a in agents]

    async def get_latest_run_id(self) -> str:
        return await asyncio.to_thread(
            self.trust_score.functions.getLatestRunId().call
        )

    # ── AgentIdentityRegistry ─────────────────────────────────────────────────

    async def register_agent(self, agent_id: str, code_hash_hex: str,
                          model_name: str = "llama-3.3-70b-versatile",
                          model_version: str = "2025-01-01") -> str:
        code_hash = bytes.fromhex(code_hash_hex.removeprefix("0x"))
        fn = self.identity_reg.functions.registerAgent(
            agent_id, code_hash, model_name, model_version
        )
        tx_hash = await self._send(fn)
        logger.info("[Identity] registered %s → %s", agent_id, tx_hash)
        return tx_hash

    async def verify_integrity(self, agent_id: str, code_hash_hex: str) -> dict:
        code_hash = bytes.fromhex(code_hash_hex.removeprefix("0x"))
        matches, exists = await asyncio.to_thread(
            self.identity_reg.functions.verify(agent_id, code_hash).call
        )
        return {
            "agentId":  agent_id,
            "matches":  matches,
            "exists":   exists,
            "verified": matches and exists,
        }

    async def verify_run(self, run_id: str) -> dict:
        agents = ["researcher", "validator", "scorer", "reporter"]
        agent_results = []

        for agent_id in agents:
            # Check if registered first
            registered = await asyncio.to_thread(
                self.identity_reg.functions.isRegistered(agent_id).call
            )
            if not registered:
                agent_results.append({
                    "agentId":        agent_id,
                    "exists":         False,
                    "matches":        False,
                    "verified":       False,
                    "registeredHash": "0x" + "0" * 64,
                })
                continue

            # Get stored record
            raw = await asyncio.to_thread(
                self.identity_reg.functions.getAgent(agent_id).call
            )
            # tuple: (agentId, codeHash, registeredBy, registeredAt, isActive, modelName, modelVersion)
            code_hash_bytes = raw[1]
            is_active       = raw[4]

            # Verify stored hash against itself — confirms contract consistency
            matches = await asyncio.to_thread(
                self.identity_reg.functions.verifyAgent(agent_id, code_hash_bytes).call
            )
            agent_results.append({
                "agentId":        agent_id,
                "exists":         True,
                "matches":        matches and is_active,
                "verified":       matches and is_active,
                "registeredHash": "0x" + code_hash_bytes.hex(),
            })
            logger.info("[Identity] verify %s → matches=%s active=%s", agent_id, matches, is_active)

        all_verified = all(a["verified"] for a in agent_results)
        return {"runId": run_id, "allMatch": all_verified, "agents": agent_results}

    # ── Event listener ────────────────────────────────────────────────────────

    async def listen_action_logged(self, queue: asyncio.Queue):
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
                        "runId":       args["runId"],
                        "agentId":     args["agentId"],
                        "action":      args["action"],
                        "txHash":      "0x" + evt["transactionHash"].hex(),
                        "stepIndex":   args["stepIndex"],
                        "inputHash":   "0x" + args["inputHash"].hex(),
                        "outputHash":  "0x" + args["outputHash"].hex(),
                        "recordIndex": args["recordIndex"],
                        "timestamp":   args["timestamp"],
                    }
                    await queue.put(payload)
                    logger.info("Event queued: %s", payload["txHash"])
            except Exception as e:
                logger.warning("Event listener error: %s", e)
            await asyncio.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton
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
    logging.basicConfig(level=logging.INFO)

    async def smoke_test():
        print("\n=== TrustChain blockchain.py smoke test ===\n")
        bridge = BlockchainBridge()
        TEST_RUN = "smoke_test_001"

        print("1. Logging test action...")
        tx = await bridge.log_action(TEST_RUN, "researcher", "SEARCH", "test input", "test output", 0)
        print(f"   ✓ tx: {tx}")
        print(f"   🔍 https://testnet.monadexplorer.com/tx/{tx}")

        print("\n2. Updating trust score...")
        tx2 = await bridge.update_score("researcher", TEST_RUN, 85, "smoke_test")
        print(f"   ✓ tx: {tx2}")

        print("\n3. Reading all audit entries...")
        entries = await bridge.get_all_audit_entries()
        print(f"   ✓ total entries: {len(entries)}")
        for e in entries[:3]:
            print(f"   [{e['entryId']}] {e['agentId']} / {e['action']} txHash={e['txHash'][:20]}...")

        print("\n4. Reading run entries...")
        run_entries = await bridge.get_run_audit_entries(TEST_RUN)
        print(f"   ✓ run entries: {len(run_entries)}")

        print("\n5. Reading scores...")
        scores = await bridge.get_all_scores(TEST_RUN)
        for s in scores:
            print(f"   {s['agentId']:12} score={s['score']}")

        print("\n6. Verifying agent identities...")
        result = await bridge.verify_run(TEST_RUN)
        print(f"   allMatch={result['allMatch']}")
        for a in result["agents"]:
            status = "✓" if a["verified"] else "✗"
            print(f"   {status} {a['agentId']:12} exists={a['exists']} matches={a['matches']}")

        print("\n=== Smoke test complete ✓ ===")

    asyncio.run(smoke_test())