"""
blockchain/contracts_v2.py — shared V2 ABI/address loading + connection
building, used by both anchor_worker/chain.py and indexer/chain.py.

Deliberately NOT cached and NOT reading get_settings() itself (unlike
anchor_worker/chain.py's get_w3()/get_signer()/get_audit_log_contract()):
those are each package's own lru_cache singleton, built from whatever
settings that process resolved, and tests monkeypatch each package's own
get_settings reference to redirect them at a local Anvil. Caching here too
would mean the anchor worker and indexer processes (which usually run
against the same chain, but don't have to) share a single memoized
instance across two otherwise-independent packages — a surprising coupling
for no real benefit, since each caller already caches at its own layer.

## Address resolution: registry-backed, not a hardcoded file

addresses_v2.json is a *bootstrap* file, not the source of truth for
generation addresses. It supplies exactly one address this module cannot
get any other way — TrustChainRegistry's own — because you need some
address to start from before you can ask a contract anything. Every other
V2 address (AgentAuditLogV2, TrustScoreRegistryV2,
AgentIdentityRegistryV2) is resolved on-chain via
TrustChainRegistry.getCurrentDeployment(), which is what the registry
exists for (see contracts/src/v2/TrustChainRegistry.sol's docstring and
plan §12.1/§12.4) — "SDKs and indexers discover current deployments
without hardcoding them" was the design intent from the start, but until
this fix nothing actually called the registry: build_contract() read all
four addresses straight out of the json file, leaving the deployed
registry write-only. write_v2_addresses.py/DeployV2.s.sol still populate
the other three keys in that file too, purely as a operator-facing record
of what a deployment produced — this module no longer reads them.

A version bump (a new generation registered + setCurrentVersion advanced)
now needs no code change and no redeploy of anything reading this module
— every process resolves the current generation's addresses fresh from
the registry the next time its (lru_cache'd, per §-above) getter runs.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Union

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from blockchain.resilient_provider import FallbackHTTPProvider

CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"

# The three generation contracts resolved via TrustChainRegistry.
# TrustChainRegistry itself is deliberately excluded — see module
# docstring on why its address is the one bootstrap exception.
_REGISTRY_RESOLVED_CONTRACTS = ("AgentAuditLogV2", "TrustScoreRegistryV2", "AgentIdentityRegistryV2")


def load_abi(name: str) -> list:
    with open(CONTRACTS_DIR / f"{name}.json") as f:
        return json.load(f)["abi"]


def load_addresses() -> dict:
    """Raw contents of addresses_v2.json — the deploy-time bootstrap
    record. Callers wanting a *current* generation address should go
    through build_contract()/resolve_current_deployment() instead, which
    resolve AgentAuditLogV2/TrustScoreRegistryV2/AgentIdentityRegistryV2
    via TrustChainRegistry rather than trusting this file to still be
    accurate (it isn't, once a new generation is registered on-chain
    without anyone updating this file — the whole point of having a
    registry). Still used directly for TrustChainRegistry's own bootstrap
    address, and by operator tooling that wants the raw deploy record."""
    path = CONTRACTS_DIR / "addresses_v2.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — deploy V2 contracts first "
            "(forge script script/DeployV2.s.sol --broadcast) and record "
            "the printed addresses there."
        )
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=8)
def _fetch_current_deployment(w3: Web3, registry_address: str) -> tuple[str, str, str]:
    """One on-chain read of TrustChainRegistry.getCurrentDeployment(),
    memoized per (w3 instance, registry address) so the three per-
    contract getters in anchor_worker/chain.py and indexer/chain.py each
    calling build_contract() don't each trigger their own round trip.
    Keyed on id(w3) via Web3's default identity hash — safe here because
    every caller already holds its own w3 behind its own lru_cache'd
    get_w3() (see module docstring), so the same w3 instance always means
    the same chain/settings, never a stale cross-process one."""
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(registry_address), abi=load_abi("TrustChainRegistry")
    )
    try:
        deployment = registry.functions.getCurrentDeployment().call()
    except Exception as e:
        raise RuntimeError(
            f"TrustChainRegistry at {registry_address} has no current deployment registered "
            "(getCurrentDeployment() reverted) — deploy V2 contracts first "
            "(forge script script/DeployV2.s.sol --broadcast), which registers and "
            "activates the deployed generation as part of that same script run."
        ) from e
    audit_log, trust_score, identity_registry, _registered_at = deployment
    return audit_log, trust_score, identity_registry


def resolve_current_deployment(w3: Web3) -> dict:
    """AgentAuditLogV2/TrustScoreRegistryV2/AgentIdentityRegistryV2
    addresses for whichever generation TrustChainRegistry currently
    points at, read live from chain rather than from addresses_v2.json."""
    registry_address = load_addresses()["TrustChainRegistry"]
    audit_log, trust_score, identity_registry = _fetch_current_deployment(w3, registry_address)
    return {
        "AgentAuditLogV2": audit_log,
        "TrustScoreRegistryV2": trust_score,
        "AgentIdentityRegistryV2": identity_registry,
    }


def build_w3(
    rpc_url: Union[str, list[str]],
    call_timeout_seconds: float = 10.0,
    retry_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    retry_max_delay_seconds: float = 2.0,
) -> Web3:
    """Always builds a FallbackHTTPProvider (see blockchain/
    resilient_provider.py), even for a single URL: endpoints after the
    first are only ever used if an earlier one's circuit breaker trips, so
    a single-element list behaves the same as a bare HTTPProvider always
    did EXCEPT for the retry-with-jitter + explicit per-call timeout (F13)
    FallbackHTTPProvider now also provides — which single-endpoint configs
    (no *_RPC_FALLBACK_URLS set, still this codebase's default) need too,
    not just multi-endpoint ones. The four keyword args default to the
    same values config.py's Settings fields do; callers that already
    resolve their own settings (this module deliberately doesn't call
    get_settings() itself — see the module docstring) should pass those
    through explicitly rather than relying on the defaults staying in
    sync."""
    urls = [rpc_url] if isinstance(rpc_url, str) else list(rpc_url)
    provider = FallbackHTTPProvider(
        urls,
        request_kwargs={"timeout": call_timeout_seconds},
        retry_max_attempts=retry_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        retry_max_delay_seconds=retry_max_delay_seconds,
    )
    w3 = Web3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"cannot connect to RPC: {urls}")
    return w3


def build_contract(w3: Web3, contract_name: str, address_key: str | None = None):
    """`address_key` defaults to `contract_name` — separate only because
    ABI filenames and addresses_v2.json's keys happen to match 1:1 today
    but there's no structural reason they must.

    TrustChainRegistry is the one contract whose address still comes
    straight from addresses_v2.json (it's the bootstrap root — nothing
    resolves *its* address for it). Every other V2 contract is resolved
    via TrustChainRegistry.getCurrentDeployment() — see
    resolve_current_deployment() and the module docstring."""
    key = address_key or contract_name
    # Resolve the address BEFORE loading the ABI: an unknown key should
    # fail with this function's own clear error, not with load_abi()'s
    # unrelated FileNotFoundError for whatever contract_name happened to
    # be passed alongside it.
    if key == "TrustChainRegistry":
        address = load_addresses()["TrustChainRegistry"]
    elif key in _REGISTRY_RESOLVED_CONTRACTS:
        address = resolve_current_deployment(w3)[key]
    else:
        raise ValueError(
            f"build_contract: no address resolution rule for {key!r} — add it to "
            "_REGISTRY_RESOLVED_CONTRACTS if it's a new registry-tracked generation "
            "contract, or handle it explicitly if it's a bootstrap address like "
            "TrustChainRegistry's."
        )
    abi = load_abi(contract_name)
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
