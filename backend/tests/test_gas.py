"""
Tests for blockchain/gas.py — the shared EIP-1559-or-legacy fee helper
(F8) used by every write path: anchor_worker/submit.py (inline, RBF-aware
variant), blockchain/score_writer.py, blockchain/identity_writer.py, and
V1's blockchain/client.py.
"""

import asyncio

from blockchain.gas import build_fee_params
from tests.conftest import requires_anvil


def run(coro):
    return asyncio.run(coro)


@requires_anvil
def test_build_fee_params_uses_eip1559_against_real_anvil():
    # Anvil exposes baseFeePerGas by default (post-London genesis), so
    # this must take the dynamic-fee branch, not the legacy fallback.
    from anchor_worker import chain as chain_module

    w3 = chain_module.get_w3()
    params = run(build_fee_params(w3))

    assert set(params.keys()) == {"maxPriorityFeePerGas", "maxFeePerGas"}
    assert params["maxPriorityFeePerGas"] > 0
    assert params["maxFeePerGas"] > params["maxPriorityFeePerGas"]


class _FakeEth:
    def __init__(self, base_fee):
        self._base_fee = base_fee
        self.gas_price = 42

    def get_block(self, _identifier):
        block = {}
        if self._base_fee is not None:
            block["baseFeePerGas"] = self._base_fee
        return block


class _FakeW3:
    def __init__(self, base_fee):
        self.eth = _FakeEth(base_fee)

    def to_wei(self, value, unit):
        assert unit == "gwei"
        return int(value * 1_000_000_000)


def test_build_fee_params_falls_back_to_legacy_when_no_base_fee():
    # A pre-EIP-1559 chain (or one whose "latest" block just doesn't carry
    # baseFeePerGas) must get a plain gasPrice tx, not a dynamic-fee one
    # with nonsense fields. No real chain in this repo's local stack lacks
    # EIP-1559 support (Anvil always has it), so this branch is exercised
    # with a minimal fake rather than real infra — the real-Anvil test
    # above already covers the branch that DOES matter against a real
    # chain; this one is pure, deterministic branch coverage for the one
    # this repo's infra can't produce on demand.
    fake_w3 = _FakeW3(base_fee=None)
    params = run(build_fee_params(fake_w3))
    assert params == {"gasPrice": 42}


def test_build_fee_params_maxfee_covers_2x_base_fee_plus_tip():
    fake_w3 = _FakeW3(base_fee=1_000_000_000)  # 1 gwei
    params = run(build_fee_params(fake_w3, priority_fee_gwei=1.0))
    assert params["maxPriorityFeePerGas"] == 1_000_000_000
    assert params["maxFeePerGas"] == 1_000_000_000 * 2 + 1_000_000_000
