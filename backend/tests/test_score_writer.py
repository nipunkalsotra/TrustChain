"""
Integration test for blockchain/score_writer.py — the module agents/
scorer.py now calls instead of V1's bridge.update_score(). Runs a real
updateScore() transaction against local Anvil and confirms it lands
exactly where TrustScoreRegistryV2 stores it, not just that the call
didn't raise.
"""

import asyncio
import uuid

from tests.conftest import requires_anvil


def run(coro):
    return asyncio.run(coro)


def _unique_run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@requires_anvil
def test_write_score_lands_on_chain(chain_settings):
    # score_writer imports get_w3/get_signer/get_trust_score_contract
    # directly from anchor_worker.chain (`from anchor_worker.chain import
    # ...`), so chain_settings' monkeypatch of anchor_worker.chain.get_settings
    # + cache_clear() already takes effect here — same function objects,
    # no separate patching needed.
    from anchor_worker import chain as chain_module
    from blockchain import score_writer

    run_id = _unique_run_id("run_write_score_test")
    tx_hash = run(score_writer.write_score("reporter", run_id, 91, "unit_test_reason"))

    assert tx_hash.startswith("0x")

    contract = chain_module.get_trust_score_contract()
    stored_score = contract.functions.scores("reporter", run_id).call()
    assert stored_score == 91
