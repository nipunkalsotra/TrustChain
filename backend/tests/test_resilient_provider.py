"""
tests/test_resilient_provider.py — CircuitBreaker unit tests plus
FallbackHTTPProvider tested against a REAL Anvil (the "good" endpoint)
and a real closed TCP port (the "bad" one, nothing listening — a genuine
connection-refused failure, not a mocked exception), matching this
project's "verify against real infra" discipline for anything touching
the network.

Requires a real Anvil reachable at localhost:8545; skipped otherwise.
"""

import time

import httpx
import pytest
from web3 import Web3

from blockchain.contracts_v2 import build_w3
from blockchain.resilient_provider import AllEndpointsUnavailableError, CircuitBreaker, FallbackHTTPProvider

ANVIL_RPC = "http://localhost:8545"
DEAD_RPC = "http://localhost:19999"  # nothing listens here — real connection-refused


def _anvil_is_up() -> bool:
    try:
        r = httpx.post(ANVIL_RPC, json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}, timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


requires_anvil = pytest.mark.skipif(not _anvil_is_up(), reason=f"no Anvil reachable at {ANVIL_RPC}")


# ── CircuitBreaker — pure unit tests, no network ───────────────────────────

def test_circuit_breaker_starts_closed():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
    assert breaker.state == "closed"
    assert breaker.allow_request() is True


def test_circuit_breaker_opens_after_threshold_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"  # 2 < threshold of 3
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow_request() is False


def test_circuit_breaker_success_resets_failure_count():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=30)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"  # only 2 consecutive since the reset


def test_circuit_breaker_half_opens_after_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.05)
    breaker.record_failure()
    assert breaker.state == "open"
    time.sleep(0.1)
    assert breaker.state == "half_open"
    assert breaker.allow_request() is True


def test_circuit_breaker_reopens_on_failure_after_half_open_trial_fails():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.1)
    assert breaker.state == "half_open"
    breaker.record_failure()
    assert breaker.state == "open"


# ── FallbackHTTPProvider — real network, real Anvil, real dead port ───────

@requires_anvil
def test_fallback_provider_uses_primary_when_healthy():
    provider = FallbackHTTPProvider([ANVIL_RPC, DEAD_RPC])
    w3 = Web3(provider)
    assert w3.eth.chain_id == 31337
    assert provider.breakers[0].state == "closed"
    assert provider.breakers[1].state == "closed"  # never even tried


@requires_anvil
def test_fallback_provider_fails_over_to_second_endpoint():
    provider = FallbackHTTPProvider([DEAD_RPC, ANVIL_RPC], failure_threshold=5)
    w3 = Web3(provider)
    # The dead endpoint is tried first (real connection-refused), then
    # transparently falls through to the real Anvil — the caller never
    # sees an error, just a working RPC call.
    assert w3.eth.chain_id == 31337
    assert provider.breakers[1].state == "closed"


@requires_anvil
def test_fallback_provider_opens_breaker_after_repeated_failures_then_skips_dead_endpoint():
    provider = FallbackHTTPProvider([DEAD_RPC, ANVIL_RPC], failure_threshold=2, reset_timeout_seconds=30)

    for _ in range(2):
        assert Web3(provider).eth.chain_id == 31337  # still succeeds via fallback each time

    assert provider.breakers[0].state == "open"

    # A fast, real timing check: with the dead endpoint's breaker open,
    # this call skips it entirely rather than paying its connection-level
    # failure cost again — should complete quickly.
    started = time.monotonic()
    assert Web3(provider).eth.chain_id == 31337
    assert time.monotonic() - started < 2.0


def test_fallback_provider_raises_when_every_endpoint_is_down():
    provider = FallbackHTTPProvider(["http://localhost:19999", "http://localhost:19998"])
    w3 = Web3(provider)
    with pytest.raises(AllEndpointsUnavailableError):
        w3.eth.chain_id


def test_fallback_provider_raises_when_every_breaker_is_open():
    provider = FallbackHTTPProvider(["http://localhost:19999"], failure_threshold=1, reset_timeout_seconds=30)
    w3 = Web3(provider)
    with pytest.raises(AllEndpointsUnavailableError):
        w3.eth.chain_id
    assert provider.breakers[0].state == "open"
    # Second call: breaker already open, must fail fast without even
    # attempting the dead endpoint again.
    started = time.monotonic()
    with pytest.raises(AllEndpointsUnavailableError):
        w3.eth.chain_id
    assert time.monotonic() - started < 0.5


def test_fallback_provider_requires_at_least_one_url():
    with pytest.raises(ValueError):
        FallbackHTTPProvider([])


# ── build_w3 — single URL vs list ──────────────────────────────────────────

@requires_anvil
def test_build_w3_accepts_single_url_unchanged():
    w3 = build_w3(ANVIL_RPC)
    assert w3.eth.chain_id == 31337
    assert not isinstance(w3.provider, FallbackHTTPProvider)


@requires_anvil
def test_build_w3_accepts_url_list_and_fails_over():
    w3 = build_w3([DEAD_RPC, ANVIL_RPC])
    assert isinstance(w3.provider, FallbackHTTPProvider)
    assert w3.eth.chain_id == 31337


@requires_anvil
def test_build_w3_single_element_list_behaves_like_single_url():
    w3 = build_w3([ANVIL_RPC])
    assert not isinstance(w3.provider, FallbackHTTPProvider)
    assert w3.eth.chain_id == 31337


# ── sample_breaker_states — the observability bridge ──────────────────────

def test_sample_breaker_states_empty_for_plain_provider():
    from blockchain.resilient_provider import sample_breaker_states

    w3 = Web3(Web3.HTTPProvider(DEAD_RPC))
    assert sample_breaker_states(w3) == {}


@requires_anvil
def test_sample_breaker_states_reflects_real_breaker_state():
    from blockchain.resilient_provider import sample_breaker_states

    provider = FallbackHTTPProvider([DEAD_RPC, ANVIL_RPC], failure_threshold=1, reset_timeout_seconds=30)
    w3 = Web3(provider)

    assert sample_breaker_states(w3) == {DEAD_RPC: "closed", ANVIL_RPC: "closed"}

    w3.eth.chain_id  # dead endpoint fails once, threshold=1 trips it immediately
    assert sample_breaker_states(w3) == {DEAD_RPC: "open", ANVIL_RPC: "closed"}
