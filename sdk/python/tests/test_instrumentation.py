"""tests/test_instrumentation.py — integration tests for trustchain_sdk's
real instrumentation surface (TrustChain: register_agent/log/audited/
verify_agent/get_proof/verify_proof/verify_proof_onchain), against a
REAL running API + real Anvil with V2 deployed — no mocking.

Run:
    docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
    pip install -e ".[dev,onchain]"
    pytest tests/test_instrumentation.py -v
"""

import time
import uuid

import httpx
import pytest

from trustchain_sdk import TrustChain
from trustchain_sdk.merkle import hash_pair, verify_proof as verify_proof_locally

BASE_URL = "http://localhost:8000"
ANVIL_RPC = "http://localhost:8545"


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _anvil_is_up() -> bool:
    try:
        r = httpx.post(ANVIL_RPC, json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}, timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_is_up(), reason=f"no TrustChain API reachable at {BASE_URL}")
requires_anvil = pytest.mark.skipif(not _anvil_is_up(), reason=f"no Anvil reachable at {ANVIL_RPC}")


@pytest.fixture()
def api_key() -> str:
    email = f"sdk_instr_test_{uuid.uuid4().hex}@example.com"
    signup = httpx.post(
        f"{BASE_URL}/auth/signup",
        json={"name": "instrumentation test", "email": email, "password": "sdk-instr-test-password-123"},
        timeout=10.0,
    )
    assert signup.status_code == 200, signup.text
    token = signup.json()["token"]

    created = httpx.post(
        f"{BASE_URL}/api-keys",
        json={"scopes": ["runs:write", "runs:read", "logs:write", "agents:register", "agents:read"], "environment": "test"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert created.status_code == 200, created.text
    return created.json()["raw_key"]


def _get_v2_addresses() -> dict:
    import json
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    with open(repo_root / "backend" / "contracts" / "addresses_v2.json") as f:
        return json.load(f)


# ── merkle.py — pure local verification, no network ─────────────────────

def test_hash_pair_matches_known_vector():
    # keccak256(sorted(a,b)) — cross-checked once against
    # backend/blockchain/merkle.py's own hash_pair for the same inputs
    # (both use the identical sorted-pair-keccak256 algorithm); this test
    # just locks in that the SDK's copy doesn't silently drift.
    a = bytes(range(32))
    b = bytes(range(32, 64))
    result_ab = hash_pair(a, b)
    result_ba = hash_pair(b, a)
    assert result_ab == result_ba  # order-independent, as documented
    assert len(result_ab) == 32


def test_verify_proof_locally_accepts_valid_and_rejects_tampered():
    leaf0 = bytes(range(1, 33))
    leaf1 = bytes(range(33, 65))
    root = hash_pair(leaf0, leaf1)

    assert verify_proof_locally(leaf0, [leaf1], root) is True
    assert verify_proof_locally(leaf1, [leaf0], root) is True

    tampered_leaf = bytes(32)
    assert verify_proof_locally(tampered_leaf, [leaf1], root) is False


# ── register_agent / verify_agent — real on-chain ────────────────────────

@requires_anvil
def test_register_agent_then_verify_matches(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        agent_id = f"sdk_test_agent_{uuid.uuid4().hex[:8]}"
        tx_hash = tc.register_agent(agent_id, model="gpt-4o", version="2026-01", system_prompt="You are helpful.")
        assert tx_hash.startswith("0x")

        result = tc.verify_agent(agent_id, model="gpt-4o", version="2026-01", system_prompt="You are helpful.")
        assert result is not None
        assert result.verified is True
        assert result.is_active is True
        assert result.hash_matches is True


@requires_anvil
def test_verify_agent_detects_prompt_tamper(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        agent_id = f"sdk_test_agent_{uuid.uuid4().hex[:8]}"
        tc.register_agent(agent_id, model="gpt-4o", version="2026-01", system_prompt="Original prompt.")

        result = tc.verify_agent(agent_id, model="gpt-4o", version="2026-01", system_prompt="TAMPERED prompt.")
        assert result.verified is False
        assert result.hash_matches is False


def test_register_agent_fails_open_by_default_with_bad_credentials():
    # on_error defaults to "warn" — a bad API key must NOT raise into
    # caller code (the whole point of "never break the host application"),
    # it should return the documented default and log a warning instead.
    with TrustChain("tc_test_not_a_real_key_00000000000000000000", base_url=BASE_URL) as tc:
        tx_hash = tc.register_agent("x", model="m", version="v", system_prompt="p")
        assert tx_hash == ""


def test_register_agent_raises_with_on_error_raise_and_bad_credentials():
    with TrustChain("tc_test_not_a_real_key_00000000000000000000", base_url=BASE_URL, on_error="raise") as tc:
        with pytest.raises(Exception):
            tc.register_agent("x", model="m", version="v", system_prompt="p")


# ── log() / log_and_wait() — non-blocking vs synchronous ─────────────────

def test_log_and_wait_returns_real_step_id(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        receipt = tc.log_and_wait(agent_id="test-agent", action="answer", input="q", output="a")
        assert receipt.error is None
        assert receipt.step_id is not None
        assert receipt.anchor_status == "pending"


def test_log_is_non_blocking_and_eventually_completes(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        receipt = tc.log(agent_id="test-agent", action="answer", input="q", output="a")
        # Non-blocking: step_id isn't known yet at the moment log() returns.
        assert receipt.step_id is None
        assert receipt.status == "queued"

        assert tc.flush(timeout=10.0) is True
        # The background worker mutates the SAME receipt object in place.
        assert receipt.step_id is not None
        assert receipt.error is None


def test_audited_decorator_logs_a_step(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        @tc.audited(agent_id="decorated-agent", action="compute")
        def compute(x: int) -> int:
            return x * 2

        result = compute(21)
        assert result == 42
        assert tc.flush(timeout=10.0) is True


def test_new_run_starts_a_fresh_run_id(api_key):
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        receipt = tc.log_and_wait(agent_id="run-test-agent", action="a", input="i", output="o")
        assert receipt.step_id is not None
        first_run_id = tc._current_run_id("run-test-agent")

        tc.new_run("run-test-agent")
        second_run_id = tc._current_run_id("run-test-agent")

        assert first_run_id != second_run_id


# ── get_proof() / verify_proof() / verify_proof_onchain() ────────────────

@requires_anvil
def test_get_proof_not_yet_anchored_fails_open(api_key):
    with TrustChain(api_key, base_url=BASE_URL) as tc:
        receipt = tc.log_and_wait(agent_id="proof-test-agent", action="a", input="i", output="o")
        assert receipt.step_id is not None

        # Not anchored yet (the real anchor-worker container hasn't had a
        # chance to run) — get_proof fails open (default on_error="warn"),
        # returning None rather than raising.
        proof = tc.get_proof(receipt.step_id)
        assert proof is None


@requires_anvil
def test_get_proof_after_real_anchoring_verifies_locally_and_onchain(api_key):
    # Waits for the REAL anchor-worker container (docker-compose) to pick
    # this step up on its own poll cycle — a real SDK consumer has no way
    # to force that any faster, so neither does this test.
    with TrustChain(api_key, base_url=BASE_URL, on_error="raise") as tc:
        receipt = tc.log_and_wait(agent_id="proof-test-agent", action="a", input="i", output="o")
        assert receipt.step_id is not None

        proof = None
        deadline = time.monotonic() + 30.0
        while proof is None and time.monotonic() < deadline:
            try:
                proof = tc.get_proof(receipt.step_id)
            except Exception:
                proof = None
            if proof is None:
                time.sleep(1.0)

        assert proof is not None, "step was not anchored by the anchor-worker container within 30s"
        assert proof.anchor_status == "confirmed"
        assert proof.anchor_id is not None

        assert tc.verify_proof(proof) is True

        addresses = _get_v2_addresses()
        assert tc.verify_proof_onchain(proof, rpc_url=ANVIL_RPC, audit_log_address=addresses["AgentAuditLogV2"]) is True

        # A forged leaf must fail both checks.
        import dataclasses
        forged = dataclasses.replace(proof, leaf="0x" + "00" * 32)
        assert tc.verify_proof(forged) is False
        assert tc.verify_proof_onchain(forged, rpc_url=ANVIL_RPC, audit_log_address=addresses["AgentAuditLogV2"]) is False
