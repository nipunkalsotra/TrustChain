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

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

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


def build_w3(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"cannot connect to RPC: {rpc_url}")
    return w3


def build_contract(w3: Web3, contract_name: str, address_key: str | None = None):
    """`address_key` defaults to `contract_name` — separate only because
    ABI filenames and addresses_v2.json's keys happen to match 1:1 today
    but there's no structural reason they must."""
    addresses = load_addresses()
    abi = load_abi(contract_name)
    address = addresses[address_key or contract_name]
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
