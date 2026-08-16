"""
Integration tests for blockchain/contracts_v2.py's registry-backed address
resolution — run against a REAL Anvil instance with V2 contracts already
deployed (see backend/contracts/addresses_v2.json), same infra convention
as test_anchor_worker.py/test_indexer.py.

What this actually verifies (the gap this closes): build_contract() used
to read AgentAuditLogV2/TrustScoreRegistryV2/AgentIdentityRegistryV2
addresses straight out of addresses_v2.json, leaving the deployed
TrustChainRegistry contract write-only — nothing ever called
getCurrentDeployment() on it, contradicting its own stated purpose (see
contracts/src/v2/TrustChainRegistry.sol's docstring and plan §12.1/§12.4:
"so SDKs and indexers discover current deployments without hardcoding
them"). These tests prove resolution now actually goes through the
registry by registering a brand-new on-chain generation and confirming
resolution follows it WITHOUT touching addresses_v2.json — a mock
couldn't demonstrate this, since the whole defect was "the code never
calls the real contract".
"""

import pytest
from web3 import Web3

from blockchain.contracts_v2 import (
    _fetch_current_deployment,
    build_contract,
    build_w3,
    load_abi,
    load_addresses,
    resolve_current_deployment,
)
from tests.conftest import ANVIL_KEY, ANVIL_RPC, requires_anvil


def _send(w3: Web3, acct, fn):
    tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address)})
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt.status == 1, receipt
    return receipt


@requires_anvil
def test_resolve_current_deployment_matches_registered_deployment():
    """The addresses resolve_current_deployment() returns must be exactly
    what TrustChainRegistry.getCurrentDeployment() reports on-chain right
    now — not a cached guess and not addresses_v2.json's bootstrap
    record (which may have drifted once a new generation is registered;
    see the next test)."""
    w3 = build_w3(ANVIL_RPC)
    registry_address = load_addresses()["TrustChainRegistry"]
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address), abi=load_abi("TrustChainRegistry")
    )

    on_chain = registry.functions.getCurrentDeployment().call()
    resolved = resolve_current_deployment(w3)

    assert resolved["AgentAuditLogV2"].lower() == on_chain[0].lower()
    assert resolved["TrustScoreRegistryV2"].lower() == on_chain[1].lower()
    assert resolved["AgentIdentityRegistryV2"].lower() == on_chain[2].lower()


@requires_anvil
def test_build_contract_uses_registry_not_the_json_file_for_generation_contracts():
    """Register and activate a brand-new (fake) generation directly on
    TrustChainRegistry, then prove build_contract() picks up the new
    addresses immediately — with addresses_v2.json never touched. If
    build_contract() were still reading the json file (the bug this
    closes), this would keep returning the OLD real deployment's
    addresses instead."""
    w3 = build_w3(ANVIL_RPC)
    acct = w3.eth.account.from_key(ANVIL_KEY)
    registry_address = load_addresses()["TrustChainRegistry"]
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address), abi=load_abi("TrustChainRegistry")
    )

    # registerDeployment() is one-time-per-version, permanently (by
    # design — see the contract's docstring), so a hardcoded version
    # number would only work once against a given chain: this test
    # re-run against the SAME still-running Anvil (a normal local dev
    # workflow, not just fresh-per-CI-run) would find it already taken
    # and revert. Scan for the first unused version at a base no real
    # deployment would ever reach instead.
    fake_version = 1_000_000
    while registry.functions.isRegistered(fake_version).call():
        fake_version += 1
    fake_audit = Web3.to_checksum_address("0x" + "11" * 20)
    fake_trust = Web3.to_checksum_address("0x" + "22" * 20)
    fake_identity = Web3.to_checksum_address("0x" + "33" * 20)

    original_json = dict(load_addresses())
    original_current_version = registry.functions.currentVersion().call()

    try:
        _send(w3, acct, registry.functions.registerDeployment(fake_version, fake_audit, fake_trust, fake_identity))
        _send(w3, acct, registry.functions.setCurrentVersion(fake_version))

        # Fresh w3 instance: _fetch_current_deployment is memoized per w3
        # identity (see contracts_v2.py), so re-resolving through the SAME
        # w3 used above would still be correct (it never resolved this
        # contract before) — a fresh instance additionally proves this
        # isn't dependent on cache-bypassing timing.
        w3_fresh = build_w3(ANVIL_RPC)
        audit_contract = build_contract(w3_fresh, "AgentAuditLogV2")
        trust_contract = build_contract(w3_fresh, "TrustScoreRegistryV2")
        identity_contract = build_contract(w3_fresh, "AgentIdentityRegistryV2")

        assert audit_contract.address == fake_audit
        assert trust_contract.address == fake_trust
        assert identity_contract.address == fake_identity

        # The bootstrap file itself must be untouched — this whole
        # mechanism works precisely because nothing needs to rewrite it
        # when a new generation goes live.
        assert load_addresses() == original_json
    finally:
        # Restore the registry's "current" pointer to whatever it was
        # before this test touched it, so later tests in this session
        # (and this file's own first test, if the session re-runs it)
        # keep resolving the real deployment, not the fake one.
        # setCurrentVersion is the only mutable pointer TrustChainRegistry
        # exposes; registerDeployment itself is one-time-per-version and
        # can't be undone (by design — see the contract's docstring), so
        # there is nothing to "roll back" for the fake registration beyond
        # pointing currentVersion back where it was.
        _send(w3, acct, registry.functions.setCurrentVersion(original_current_version))
        _fetch_current_deployment.cache_clear()


@requires_anvil
def test_build_contract_rejects_unknown_contract_name():
    """A contract name that's neither the TrustChainRegistry bootstrap
    nor a registry-tracked generation contract must fail loudly rather
    than silently resolving to the wrong thing."""
    w3 = build_w3(ANVIL_RPC)
    with pytest.raises(ValueError, match="no address resolution rule"):
        build_contract(w3, "SomeContractThatDoesNotExist")
