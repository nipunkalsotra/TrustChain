"""
blockchain/gas.py — shared EIP-1559-or-legacy fee pricing (F8).

Every write path here builds a transaction, so this is centralized rather
than each writer computing its own gasPrice: EIP-1559 dynamic fees are
tried first (you only ever pay the effective gas price actually needed at
mine time, not a flat gasPrice ceiling set at submission time — and some
chains reject legacy transactions outright once London-equivalent is
active), falling back to a plain legacy gasPrice only when the connected
chain doesn't expose a baseFeePerGas (e.g. a pre-EIP-1559 chain, or a
local test chain configured without it).
"""

import asyncio

from web3 import Web3


async def build_fee_params(w3: Web3, priority_fee_gwei: float = 1.0) -> dict:
    """Returns the fee fields to merge into build_transaction(...)'s
    params dict — either {maxPriorityFeePerGas, maxFeePerGas} or, on a
    chain with no EIP-1559 support, {gasPrice}. maxFeePerGas is set to
    2x the current base fee plus the tip: generous headroom so the tx
    doesn't need to be repriced for a base-fee bump between submission
    and inclusion in the next block or two, not a real budget cap (only
    the tip actually paid above base fee ever leaves the wallet)."""
    try:
        latest = await asyncio.to_thread(w3.eth.get_block, "latest")
        base_fee = latest.get("baseFeePerGas")
    except Exception:
        base_fee = None

    if base_fee is not None:
        priority_fee = w3.to_wei(priority_fee_gwei, "gwei")
        return {"maxPriorityFeePerGas": priority_fee, "maxFeePerGas": base_fee * 2 + priority_fee}

    gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
    return {"gasPrice": gas_price}
