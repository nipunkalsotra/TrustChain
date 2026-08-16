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
from web3 import HTTPProvider, Web3

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
def test_fallback_provider_records_real_rpc_call_failures_metric():
    """observability.RPC_CALL_FAILURES_TOTAL, labeled by the specific
    endpoint that failed — a real connection-refused against DEAD_RPC,
    not a mock, matching this file's own real-infra discipline. Checked
    as a delta (before/after), not an absolute value, since other tests
    in this real-network suite may have already incremented the same
    endpoint label."""
    import observability

    dead_before = observability.RPC_CALL_FAILURES_TOTAL.labels(endpoint=DEAD_RPC)._value.get()
    healthy_before = observability.RPC_CALL_FAILURES_TOTAL.labels(endpoint=ANVIL_RPC)._value.get()

    provider = FallbackHTTPProvider([DEAD_RPC, ANVIL_RPC], failure_threshold=5)
    assert Web3(provider).eth.chain_id == 31337  # succeeds via fallback

    assert observability.RPC_CALL_FAILURES_TOTAL.labels(endpoint=DEAD_RPC)._value.get() == dead_before + 1
    # The healthy endpoint never failed — no NEW failure recorded against it.
    assert observability.RPC_CALL_FAILURES_TOTAL.labels(endpoint=ANVIL_RPC)._value.get() == healthy_before


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
def test_build_w3_accepts_single_url_and_still_wraps_it_for_retry_and_timeout():
    # F13: build_w3 always returns a FallbackHTTPProvider, even for a
    # single URL — a single-endpoint config (no *_RPC_FALLBACK_URLS set,
    # still this codebase's default) needs retry-with-jitter + an
    # explicit per-call timeout too, not just multi-endpoint configs. This
    # used to assert the OPPOSITE (plain HTTPProvider for a single URL,
    # unwrapped) before F13 made FallbackHTTPProvider itself carry that
    # behavior.
    w3 = build_w3(ANVIL_RPC)
    assert w3.eth.chain_id == 31337
    assert isinstance(w3.provider, FallbackHTTPProvider)


@requires_anvil
def test_build_w3_accepts_url_list_and_fails_over():
    w3 = build_w3([DEAD_RPC, ANVIL_RPC])
    assert isinstance(w3.provider, FallbackHTTPProvider)
    assert w3.eth.chain_id == 31337


@requires_anvil
def test_build_w3_single_element_list_behaves_like_single_url():
    w3 = build_w3([ANVIL_RPC])
    assert isinstance(w3.provider, FallbackHTTPProvider)
    assert w3.eth.chain_id == 31337


# ── Retry-with-jitter + per-call timeout (F13) ─────────────────────────────
#
# A real dead port (DEAD_RPC) proves retries happen at all and that a
# whole retried-out call still counts as exactly ONE breaker/metric
# failure. Proving the TIMEOUT itself needs something DEAD_RPC can't
# provide — connection-refused fails near-instantly, it never exercises
# request_kwargs={"timeout": ...} at all. _hanging_server below is a real
# TCP listener that accepts the connection and then never responds,
# forcing an actual client-side read-timeout.

import http.server
import threading


class _HangForever(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        threading.Event().wait(30)  # never actually reached within any test's own timeout

    def log_message(self, *args):
        pass  # keep test output clean


class _HangingServer:
    """Binds an ephemeral local port, accepts connections, never responds
    — a real slow/dead RPC endpoint, not a mocked timeout."""

    def __enter__(self):
        self._httpd = http.server.HTTPServer(("127.0.0.1", 0), _HangForever)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()


def test_call_timeout_cuts_off_a_real_hanging_endpoint():
    with _HangingServer() as server:
        provider = FallbackHTTPProvider(
            [server.url], request_kwargs={"timeout": 0.5}, retry_max_attempts=1,
        )
        w3 = Web3(provider)
        started = time.monotonic()
        with pytest.raises(AllEndpointsUnavailableError):
            w3.eth.chain_id
        elapsed = time.monotonic() - started
        # Real client-side timeout at ~0.5s, not the server's 30s sleep —
        # generous upper bound for CI scheduling jitter, still nowhere
        # near 30s if the timeout is actually being applied.
        assert elapsed < 5.0


def test_retry_with_jitter_retries_the_same_endpoint_before_failing_over():
    calls = []
    real_make_request = HTTPProvider.make_request

    def _counting_make_request(self, method, params):
        calls.append(self.endpoint_uri)
        return real_make_request(self, method, params)

    provider = FallbackHTTPProvider(
        [DEAD_RPC], retry_max_attempts=3, retry_base_delay_seconds=0.01, retry_max_delay_seconds=0.02,
    )
    for p in provider._providers:
        p.make_request = _counting_make_request.__get__(p, HTTPProvider)

    with pytest.raises(AllEndpointsUnavailableError):
        Web3(provider).eth.chain_id

    # 3 real attempts against the SAME dead endpoint before giving up.
    assert len(calls) == 3
    assert all(c == DEAD_RPC for c in calls)


def test_retry_with_jitter_counts_as_one_breaker_failure_not_one_per_attempt():
    provider = FallbackHTTPProvider(
        [DEAD_RPC], failure_threshold=2, retry_max_attempts=3,
        retry_base_delay_seconds=0.01, retry_max_delay_seconds=0.02,
    )
    with pytest.raises(AllEndpointsUnavailableError):
        Web3(provider).eth.chain_id
    # 3 retries happened (proven by the timing/count test above), but the
    # breaker only saw it as ONE failure — still closed, not yet at
    # failure_threshold=2's open state.
    assert provider.breakers[0].state == "closed"
    with pytest.raises(AllEndpointsUnavailableError):
        Web3(provider).eth.chain_id
    assert provider.breakers[0].state == "open"


def test_retry_with_jitter_backoff_actually_delays_between_attempts():
    provider = FallbackHTTPProvider(
        [DEAD_RPC], retry_max_attempts=3, retry_base_delay_seconds=0.2, retry_max_delay_seconds=1.0,
    )
    started = time.monotonic()
    with pytest.raises(AllEndpointsUnavailableError):
        Web3(provider).eth.chain_id
    elapsed = time.monotonic() - started
    # 2 real backoff sleeps between 3 attempts (jittered, so not exact) —
    # this is a real wall-clock delay, not zero, confirming retries aren't
    # firing back-to-back with no pause at all.
    assert elapsed > 0.05


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
