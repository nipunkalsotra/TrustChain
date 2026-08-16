"""
Tests for blockchain/gas.py — the shared EIP-1559-or-legacy fee helper
(F8) used by every write path: anchor_worker/submit.py (inline, RBF-aware
variant), blockchain/score_writer.py, blockchain/identity_writer.py, and
V1's blockchain/client.py — and estimate_gas_with_margin, the real
eth_estimateGas-based gas-limit helper that replaced flat hardcoded gas
limits across those same write paths.
"""

import asyncio

from blockchain.gas import build_fee_params, estimate_gas_with_margin
from tests.conftest import requires_anvil


def run(coro):
    return asyncio.run(coro)


@requires_anvil
def test_build_fee_params_uses_eip1559_against_real_anvil():
    # Anvil exposes baseFeePerGas by default (post-London genesis), so
    # this must take the dynamic-fee branch, not the legacy fallback.
    # Also proves this is genuinely fee-HISTORY-based against a real
    # node, not just a mocked shape: eth_feeHistory is a distinct RPC
    # method from eth_getBlockByNumber, and Anvil must actually support
    # it for this to return sane values at all.
    from anchor_worker import chain as chain_module

    w3 = chain_module.get_w3()
    params = run(build_fee_params(w3))

    assert set(params.keys()) == {"maxPriorityFeePerGas", "maxFeePerGas"}
    assert params["maxPriorityFeePerGas"] > 0
    assert params["maxFeePerGas"] > params["maxPriorityFeePerGas"]


@requires_anvil
def test_estimate_gas_with_margin_against_real_anvil_updateScore(chain_settings):
    # A real ABI-encoded call through a real deployed contract — proves
    # this isn't just "returns the fallback", the underlying
    # eth_estimateGas round trip actually succeeds and returns something
    # sane for a real transaction. Needs the chain_settings fixture (not
    # just requires_anvil) because updateScore is ANCHOR_ROLE-gated, and
    # only the fixture's ANVIL_KEY signer — the same one DeployV2.s.sol's
    # local-dev default (RELAYER_ADDRESS unset) grants that role to — can
    # call it; get_settings()'s real PRIVATE_KEY is a different, unrelated
    # V1-testnet-deployer key with no role on this local Anvil deployment.
    from anchor_worker import chain as chain_module

    contract = chain_module.get_trust_score_contract()
    signer = chain_module.get_signer()
    fn = contract.functions.updateScore("researcher", "gas_estimate_test_run", 80, "test")

    gas = run(estimate_gas_with_margin(fn, signer.address, margin=1.2, fallback=999_999))
    # Ceiling generous enough to cover the first-ever call's cold SSTOREs
    # (fresh contract, every storage slot this touches — score, reason,
    # this run's agent list — starts zeroed, ~20k gas per slot instead of
    # a warm update's ~5k) while still being nowhere near the fallback,
    # which is what actually proves the real RPC estimate was used rather
    # than the fallback path.
    assert 0 < gas < 500_000


def test_estimate_gas_with_margin_falls_back_on_estimation_failure():
    class _BrokenFn:
        def estimate_gas(self, _params):
            raise RuntimeError("simulated eth_estimateGas failure")

    gas = run(estimate_gas_with_margin(_BrokenFn(), "0xdeadbeef00000000000000000000000000000000", fallback=123_456))
    assert gas == 123_456


class _FakeEth:
    def __init__(self, base_fee, rewards=None):
        self._base_fee = base_fee
        self._rewards = rewards if rewards is not None else [[0]]
        self.gas_price = 42

    def fee_history(self, _block_count, _newest_block, _percentiles):
        if self._base_fee is None:
            raise ValueError("eth_feeHistory not supported on this chain")
        return {"baseFeePerGas": [self._base_fee] * 10 + [self._base_fee], "reward": self._rewards}


class _FakeW3:
    def __init__(self, base_fee, rewards=None):
        self.eth = _FakeEth(base_fee, rewards)

    def to_wei(self, value, unit):
        assert unit == "gwei"
        return int(value * 1_000_000_000)


def test_build_fee_params_falls_back_to_legacy_when_no_fee_history():
    # A pre-EIP-1559 chain (or one whose node just doesn't implement
    # eth_feeHistory) must get a plain gasPrice tx, not a dynamic-fee one
    # with nonsense fields. No real chain in this repo's local stack lacks
    # EIP-1559 support (Anvil always has it), so this branch is exercised
    # with a minimal fake rather than real infra — the real-Anvil test
    # above already covers the branch that DOES matter against a real
    # chain; this one is pure, deterministic branch coverage for the one
    # this repo's infra can't produce on demand.
    fake_w3 = _FakeW3(base_fee=None)
    params = run(build_fee_params(fake_w3))
    assert params == {"gasPrice": 42}


def test_build_fee_params_maxfee_covers_2x_projected_base_fee_plus_tip():
    # All-zero reward data (a quiet chain) must fall back to the
    # priority_fee_gwei floor, not 0 — and maxFeePerGas is computed off
    # the LAST baseFeePerGas entry (fee_history's projection for the next
    # block), not the first/current one.
    fake_w3 = _FakeW3(base_fee=1_000_000_000, rewards=[[0]])  # 1 gwei base, no real reward signal
    params = run(build_fee_params(fake_w3, priority_fee_gwei=1.0))
    assert params["maxPriorityFeePerGas"] == 1_000_000_000
    assert params["maxFeePerGas"] == 1_000_000_000 * 2 + 1_000_000_000


def test_build_fee_params_uses_median_of_real_reward_history():
    # Real (non-zero) reward samples must win over the floor — median of
    # [1, 3, 5] gwei is 3 gwei, well above the 1 gwei floor.
    rewards = [[1_000_000_000], [3_000_000_000], [5_000_000_000]]
    fake_w3 = _FakeW3(base_fee=2_000_000_000, rewards=rewards)  # 2 gwei base
    params = run(build_fee_params(fake_w3, priority_fee_gwei=1.0))
    assert params["maxPriorityFeePerGas"] == 3_000_000_000
    assert params["maxFeePerGas"] == 2_000_000_000 * 2 + 3_000_000_000
