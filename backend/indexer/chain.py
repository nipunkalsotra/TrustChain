"""
indexer/chain.py — V2 chain connection for the indexer.

Read-only (no Signer — the indexer never sends transactions), otherwise
the same shared ABI/address loading as anchor_worker/chain.py via
blockchain/contracts_v2.py.
"""

from functools import lru_cache

from web3 import Web3

from blockchain.contracts_v2 import build_contract, build_w3
from config import get_settings


@lru_cache
def get_w3() -> Web3:
    settings = get_settings()
    return build_w3(
        settings.resolved_v2_rpc_urls,
        call_timeout_seconds=settings.rpc_call_timeout_seconds,
        retry_max_attempts=settings.rpc_retry_max_attempts,
        retry_base_delay_seconds=settings.rpc_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.rpc_retry_max_delay_seconds,
    )


@lru_cache
def get_audit_log_contract():
    return build_contract(get_w3(), "AgentAuditLogV2")


@lru_cache
def get_trust_score_contract():
    return build_contract(get_w3(), "TrustScoreRegistryV2")


@lru_cache
def get_identity_registry_contract():
    return build_contract(get_w3(), "AgentIdentityRegistryV2")
