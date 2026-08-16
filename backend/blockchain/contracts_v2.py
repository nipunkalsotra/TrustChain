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
"""

import json
from pathlib import Path
from typing import Union

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from blockchain.resilient_provider import FallbackHTTPProvider

CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"


def load_abi(name: str) -> list:
    with open(CONTRACTS_DIR / f"{name}.json") as f:
        return json.load(f)["abi"]


def load_addresses() -> dict:
    path = CONTRACTS_DIR / "addresses_v2.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — deploy V2 contracts first "
            "(forge script script/DeployV2.s.sol --broadcast) and record "
            "the printed addresses there."
        )
    with open(path) as f:
        return json.load(f)


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
    but there's no structural reason they must."""
    addresses = load_addresses()
    abi = load_abi(contract_name)
    address = addresses[address_key or contract_name]
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
