"""
blockchain/identity_writer.py — synchronous agent identity writes to
AgentIdentityRegistryV2.

Registration/revocation are rare, low-volume, one-off writes (once per
agent config, not once per pipeline step — unlike step anchoring, which
is batched via the outbox + anchor worker specifically because step
volume justifies it), so this keeps the same synchronous "await the tx"
shape blockchain/score_writer.py already uses for trust scores.

Called by POST /agents and DELETE /agents/{agent_id} (main.py) using the
platform backend's own REGISTRAR_ROLE key — see
contracts/src/v2/AgentIdentityRegistryV2.sol's docstring for why that's a
role distinct from both DEFAULT_ADMIN_ROLE and ANCHOR_ROLE.
"""

import asyncio

from anchor_worker.chain import get_identity_registry_contract, get_signer, get_w3
from logging_config import get_logger

logger = get_logger(__name__)


async def register_agent(agent_id: str, code_hash: str, model: str, version: str) -> str:
    """Signs, sends, and waits for confirmation of one registerAgent()
    call. Returns the tx hash. Raises on revert or confirmation timeout —
    the caller (main.py's POST /agents) surfaces that as a 502, not a
    silently-accepted registration that never actually landed on chain."""
    w3 = get_w3()
    signer = get_signer()
    contract = get_identity_registry_contract()

    code_hash_bytes = bytes.fromhex(code_hash.removeprefix("0x"))
    fn = contract.functions.registerAgent(agent_id, code_hash_bytes, model, version)
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, signer.address, "pending")
    gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
    tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": 400_000, "gasPrice": gas_price})
    signed = signer.sign_transaction(tx)
    tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)

    receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, 60)
    if receipt.status != 1:
        raise RuntimeError(f"AgentIdentityRegistryV2.registerAgent reverted for agentId={agent_id}")

    tx_hash_hex = "0x" + tx_hash.hex()
    logger.info("agent_registered_onchain", agent_id=agent_id, tx_hash=tx_hash_hex)
    return tx_hash_hex


async def revoke_agent(agent_id: str) -> str:
    w3 = get_w3()
    signer = get_signer()
    contract = get_identity_registry_contract()

    fn = contract.functions.revokeAgent(agent_id)
    nonce = await asyncio.to_thread(w3.eth.get_transaction_count, signer.address, "pending")
    gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
    tx = fn.build_transaction({"from": signer.address, "nonce": nonce, "gas": 200_000, "gasPrice": gas_price})
    signed = signer.sign_transaction(tx)
    tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)

    receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, 60)
    if receipt.status != 1:
        raise RuntimeError(f"AgentIdentityRegistryV2.revokeAgent reverted for agentId={agent_id}")

    tx_hash_hex = "0x" + tx_hash.hex()
    logger.info("agent_revoked_onchain", agent_id=agent_id, tx_hash=tx_hash_hex)
    return tx_hash_hex


async def verify_agent(agent_id: str, current_hash: str) -> dict:
    """Read-only live verification (§7.3) — recomputes nothing itself
    (the caller already hashed the live config client-side or the
    backend recomputed it server-side; either way this just asks the
    chain), calling verifyAgentFull() so a single round trip returns
    isValid/isActive/hashMatches/storedHash together instead of needing
    three separate calls."""
    contract = get_identity_registry_contract()
    current_hash_bytes = bytes.fromhex(current_hash.removeprefix("0x"))
    result = await asyncio.to_thread(
        lambda: contract.functions.verifyAgentFull(agent_id, current_hash_bytes).call()
    )
    return {
        "agentId": agent_id,
        "isValid": result[0],
        "isActive": result[1],
        "hashMatches": result[2],
        "storedHash": "0x" + result[3].hex(),
        "providedHash": "0x" + result[4].hex(),
        "registeredAt": result[5],
    }
