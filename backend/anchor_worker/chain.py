"""
anchor_worker/chain.py — V2 write-capable chain connection.

Deliberately separate from blockchain/client.py's BlockchainBridge: that
class is Phase 1's V1 bridge, being trimmed down to read-only V1 helpers
(still used by /verify and the tamper demo) now that writes live here
instead, against the V2 contracts and addresses_v2.json. ABI/address
loading and connection-building are shared with indexer/chain.py via
blockchain/contracts_v2.py; the lru_cache singletons here are this
module's own (see that module's docstring for why they aren't shared).

Used by both the anchor worker itself (batch anchoring) and agents/
scorer.py (individual score writes) — both are V2 writers, just with very
different volume/batching needs, so they share this connection module
without sharing anything about *how* each one writes.
"""

from functools import lru_cache

from web3 import Web3

from blockchain.contracts_v2 import build_contract, build_w3
from blockchain.signer import AwsKmsSigner, GcpKmsSigner, LocalKeySigner, Signer, VaultKvSigner
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
def get_signer() -> Signer:
    """Backend selected via SIGNER_BACKEND (config.signer_backend) — see
    that field's docstring and blockchain/signer.py for the custody
    tradeoffs. Every caller (anchor worker, agents/scorer.py) depends on
    the Signer protocol, not on which backend this returns, so switching
    from a raw key to a cloud KMS in production is a config change here,
    not a code change at any call site."""
    settings = get_settings()
    backend = settings.signer_backend

    if backend == "aws_kms":
        if not settings.kms_key_id:
            raise ValueError("KMS_KEY_ID not set in .env (required for SIGNER_BACKEND=aws_kms)")
        return AwsKmsSigner(settings.kms_key_id, get_w3(), region_name=settings.kms_region)

    if backend == "gcp_kms":
        if not settings.kms_key_id:
            raise ValueError("KMS_KEY_ID not set in .env (required for SIGNER_BACKEND=gcp_kms)")
        return GcpKmsSigner(settings.kms_key_id, get_w3())

    if backend == "vault_kv":
        if not (settings.vault_addr and settings.vault_token and settings.vault_secret_path):
            raise ValueError(
                "VAULT_ADDR, VAULT_TOKEN, and VAULT_SECRET_PATH must all be set in .env "
                "(required for SIGNER_BACKEND=vault_kv)"
            )
        return VaultKvSigner(
            settings.vault_addr, settings.vault_token, settings.vault_secret_path, get_w3(),
            mount_point=settings.vault_mount_point, key_field=settings.vault_key_field,
        )

    if not settings.resolved_v2_private_key:
        raise ValueError("PRIVATE_KEY (or V2_PRIVATE_KEY) not set in .env")
    return LocalKeySigner(settings.resolved_v2_private_key, get_w3())


@lru_cache
def get_audit_log_contract():
    return build_contract(get_w3(), "AgentAuditLogV2")


@lru_cache
def get_trust_score_contract():
    return build_contract(get_w3(), "TrustScoreRegistryV2")


@lru_cache
def get_identity_registry_contract():
    return build_contract(get_w3(), "AgentIdentityRegistryV2")
