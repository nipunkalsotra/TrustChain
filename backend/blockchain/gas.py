"""
blockchain/gas.py — shared EIP-1559-or-legacy fee pricing, from real fee
history (F8), plus real per-call gas-limit estimation (also F8).

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

# How many recent blocks eth_feeHistory samples for the priority-fee
# estimate — enough to smooth over one or two unusually quiet/busy
# blocks without dragging in stale data from much earlier.
_FEE_HISTORY_BLOCK_COUNT = 10
# The reward percentile requested from eth_feeHistory: the fee a
# transaction needed to pay to land in (roughly) the middle of each
# sampled block, not the cheapest tip that happened to get lucky (a low
# percentile) or the most any single tx overpaid (a high one).
_FEE_HISTORY_REWARD_PERCENTILE = 50


async def build_fee_params(w3: Web3, priority_fee_gwei: float = 1.0) -> dict:
    """Returns the fee fields to merge into build_transaction(...)'s
    params dict — either {maxPriorityFeePerGas, maxFeePerGas} or, on a
    chain with no EIP-1559 support, {gasPrice}.

    Uses eth_feeHistory (plan F8: "fee-history-based estimation"), not a
    single latest-block read: fee_history's baseFeePerGas array's LAST
    entry is the chain's own projection for the NEXT block (that's what
    eth_feeHistory computes it for), genuinely forward-looking rather
    than "whatever the base fee happened to be last block". The priority
    fee is the median of the last _FEE_HISTORY_BLOCK_COUNT blocks'
    _FEE_HISTORY_REWARD_PERCENTILE-percentile rewards — real recent
    network conditions, not a fixed guess — floored at priority_fee_gwei
    so a quiet chain with all-zero rewards (e.g. local Anvil under no
    load) still pays a sane minimum tip instead of 0, which some clients
    reject outright. maxFeePerGas is set to 2x the projected next base
    fee plus that tip: generous headroom so the tx doesn't need to be
    repriced for a base-fee bump between submission and inclusion in the
    next block or two, not a real budget cap (only the tip actually paid
    above base fee ever leaves the wallet)."""
    try:
        history = await asyncio.to_thread(
            w3.eth.fee_history, _FEE_HISTORY_BLOCK_COUNT, "latest", [_FEE_HISTORY_REWARD_PERCENTILE]
        )
        next_base_fee = history["baseFeePerGas"][-1]
    except Exception:
        next_base_fee = None
        history = None

    if next_base_fee is not None:
        floor = w3.to_wei(priority_fee_gwei, "gwei")
        rewards = sorted(r[0] for r in history["reward"] if r[0] > 0)
        priority_fee = max(rewards[len(rewards) // 2], floor) if rewards else floor
        return {"maxPriorityFeePerGas": priority_fee, "maxFeePerGas": next_base_fee * 2 + priority_fee}

    gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
    return {"gasPrice": gas_price}


async def estimate_gas_with_margin(fn, from_address: str, margin: float = 1.2, fallback: int = 300_000) -> int:
    """Real eth_estimateGas via the contract function's own ABI-aware
    estimator, plus a safety margin — replacing a flat hardcoded gas
    limit (F8: "estimateGas plus a safety margin"). The margin absorbs
    the gap between a dry-run estimate and the real cost at inclusion
    time (state can shift between estimation and mining); it is not a
    hedge against the estimate being wrong in kind, just in degree.
    `fallback` is used only if estimation itself fails (e.g. the RPC
    doesn't support eth_estimateGas, or a transient node error) — not the
    normal path, and callers should pass their own previous hardcoded
    value here so a real estimation outage degrades to exactly today's
    behavior rather than a new failure mode."""
    try:
        estimated = await asyncio.to_thread(fn.estimate_gas, {"from": from_address})
        return int(estimated * margin)
    except Exception:
        return fallback
