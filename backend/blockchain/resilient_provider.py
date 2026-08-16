"""
blockchain/resilient_provider.py — multi-endpoint RPC failover with a
per-endpoint circuit breaker, for the Web3 connections V2 (anchor worker,
indexer, agents/scorer.py) and V1 (blockchain/client.py's BlockchainBridge)
both depend on.

WHY THIS EXISTS: every RPC call in this codebase went through a single
`Web3(HTTPProvider(one_url))` — a transient RPC outage (the testnet node
restarting, a rate limit, a network blip) took down anchoring, scoring,
and health checks with no recovery path except a process restart once the
endpoint came back. That's real production risk for a chain client, not a
hypothetical.

WHAT THIS ADDS, deliberately staying inside web3.py's own extension
point (a custom Provider) rather than wrapping every call site:

  1. FALLBACK — configure additional RPC URLs (MONAD_RPC_FALLBACK_URLS /
     V2_RPC_FALLBACK_URLS, comma-separated); a request that fails against
     the primary is retried against the next configured endpoint before
     giving up. Every existing caller (`w3.eth.get_transaction_count`,
     `contract.functions.X().call()`, etc.) is unaffected — they all funnel
     through Provider.make_request(), which is the one place this hooks in.

  2. CIRCUIT BREAKER — each endpoint gets its own CLOSED -> OPEN ->
     HALF_OPEN state machine (see CircuitBreaker). After
     `failure_threshold` consecutive failures, that endpoint is skipped
     entirely (not even attempted) for `reset_timeout_seconds`, so a dead
     primary doesn't add a full timeout's worth of latency to every single
     request while the fallback quietly handles traffic — exactly the
     failure mode a bare "try primary, catch, try fallback" retry loop
     doesn't protect against (it still pays the primary's timeout on every
     call, forever, until the primary recovers). After the cooldown, the
     next request gets one HALF_OPEN trial; success closes the breaker
     again, failure reopens it for another full cooldown.

  3. PER-CALL TIMEOUT + RETRY-WITH-JITTER (F13) — a layer UNDER #2, not a
     replacement for it: a single transient blip against the CURRENT
     endpoint (one dropped packet, one slow response just past a tight
     timeout) gets retried in place, with full-jitter exponential backoff
     between attempts, BEFORE the breaker's failure count is touched at
     all — only once every retry against that endpoint is exhausted does
     it count as the ONE failure #2's threshold logic sees. Without this,
     every transient blip counts as a full breaker failure, which could
     trip the breaker (and fail over) on a run of bad luck that individual
     retries would have absorbed. The timeout itself (`request_kwargs`,
     passed straight to web3.py's underlying HTTPProvider/requests.Session)
     replaces web3.py's own undocumented 10s default with an explicit,
     configured one — a hung connection should fail fast enough for a
     retry or failover to still be useful.

A single-endpoint config (today's default — no *_RPC_FALLBACK_URLS set)
still gets #1/#2's structure (a one-element endpoint list, one breaker
that in practice never opens against itself) — the difference from a bare
HTTPProvider is exactly #3, applied uniformly rather than only to
multi-endpoint configs.
"""

import random
import threading
import time
from typing import Any, Optional

from web3 import HTTPProvider
from web3.types import RPCEndpoint, RPCResponse

import observability


class CircuitBreaker:
    """Per-endpoint failure tracker. Not thread-safety theater — the
    anchor worker, indexer, and API can all share a Web3 instance across
    threads (web3.py itself doesn't guarantee single-threaded use), so
    state transitions are lock-protected."""

    def __init__(self, failure_threshold: int = 3, reset_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    def allow_request(self) -> bool:
        """CLOSED or HALF_OPEN (cooldown elapsed) -> True. OPEN (still
        cooling down) -> False, the caller must skip this endpoint
        entirely without attempting it."""
        with self._lock:
            if self._opened_at is None:
                return True
            return (time.monotonic() - self._opened_at) >= self.reset_timeout_seconds

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                # Refresh the timestamp even if already open: this is only
                # ever reachable when allow_request() let the call through
                # (CLOSED, or HALF_OPEN's one trial — see FallbackHTTPProvider),
                # so a failure here while already past threshold means a
                # failed half-open trial, which must re-open for a fresh
                # cooldown rather than leave the stale opened_at behind and
                # have `state` keep reporting half_open forever.
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if (time.monotonic() - self._opened_at) >= self.reset_timeout_seconds:
                return "half_open"
            return "open"


class AllEndpointsUnavailableError(ConnectionError):
    """Every configured RPC endpoint either errored or has an open circuit
    breaker — distinct from a plain ConnectionError so callers/tests can
    distinguish "every endpoint is down" from "the one endpoint is down"
    without string-matching an error message."""


class FallbackHTTPProvider(HTTPProvider):
    """A web3.py Provider that tries `endpoint_urls` in order, skipping
    any endpoint whose circuit breaker is open, raising
    AllEndpointsUnavailableError only once every endpoint has either been
    tried-and-failed or was already open."""

    def __init__(
        self,
        endpoint_urls: list[str],
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30.0,
        request_kwargs: Optional[dict] = None,
        retry_max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.25,
        retry_max_delay_seconds: float = 2.0,
    ):
        if not endpoint_urls:
            raise ValueError("FallbackHTTPProvider needs at least one endpoint URL")
        super().__init__(endpoint_urls[0], request_kwargs=request_kwargs)
        self.endpoint_urls = list(endpoint_urls)
        self._providers = [HTTPProvider(url, request_kwargs=request_kwargs) for url in endpoint_urls]
        self.breakers = [CircuitBreaker(failure_threshold, reset_timeout_seconds) for _ in endpoint_urls]
        self.retry_max_attempts = retry_max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds

    def _call_with_retry(self, provider: HTTPProvider, method: RPCEndpoint, params: Any) -> RPCResponse:
        """Retries the SAME endpoint in place, full-jitter exponential
        backoff between attempts, before the caller ever touches that
        endpoint's breaker. Re-raises the last attempt's exception once
        retries are exhausted — the caller (make_request) treats that as
        ONE failure against this endpoint, not `retry_max_attempts` of them."""
        last_error: Optional[Exception] = None
        for attempt in range(self.retry_max_attempts):
            try:
                return provider.make_request(method, params)
            except Exception as e:  # noqa: BLE001 — retried regardless of failure shape; make_request's breaker/failover layer is what needs to distinguish further
                last_error = e
                if attempt < self.retry_max_attempts - 1:
                    delay = random.uniform(0, min(self.retry_max_delay_seconds, self.retry_base_delay_seconds * (2 ** attempt)))
                    time.sleep(delay)
        raise last_error  # noqa: B904 — re-raising the original, not wrapping it; make_request's `from last_error` below is where chaining happens

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        last_error: Optional[Exception] = None
        attempted_any = False

        for i, (provider, breaker) in enumerate(zip(self._providers, self.breakers)):
            if not breaker.allow_request():
                continue
            attempted_any = True
            try:
                response = self._call_with_retry(provider, method, params)
            except Exception as e:  # noqa: BLE001 — any RPC-layer failure triggers failover
                breaker.record_failure()
                # The one exception to this module's "no metrics dependency"
                # design (see sample_breaker_states' docstring for the
                # general rule) — this is the single choke point EVERY
                # failed RPC call against a configured endpoint passes
                # through, and it's the only place that knows the failure
                # happened at all (a caller wrapping make_request() can't
                # tell "endpoint 2 of 3 failed, endpoint 3 succeeded" apart
                # from "endpoint 1 succeeded on the first try" — both look
                # identical from outside). Incrementing here, not via a
                # returned/polled value like breaker state, is what makes
                # per-endpoint failure RATE observable at all.
                observability.RPC_CALL_FAILURES_TOTAL.labels(endpoint=self.endpoint_urls[i]).inc()
                last_error = e
                continue
            breaker.record_success()
            return response

        if not attempted_any:
            raise AllEndpointsUnavailableError(
                f"all {len(self.endpoint_urls)} RPC endpoint(s) have an open circuit breaker "
                f"(cooling down after repeated failures): {self.endpoint_urls}"
            )
        raise AllEndpointsUnavailableError(
            f"all {len(self.endpoint_urls)} RPC endpoint(s) failed for {method}: {self.endpoint_urls}"
        ) from last_error

    def is_connected(self, show_traceback: bool = False) -> bool:
        return any(provider.is_connected(show_traceback=show_traceback) for provider in self._providers)


def sample_breaker_states(w3) -> dict[str, str]:
    """{endpoint_url: "closed"|"open"|"half_open"} for a Web3 instance
    built with FallbackHTTPProvider — {} for a plain single-endpoint
    provider (nothing to sample). Deliberately returns plain data rather
    than touching Prometheus directly — STATE is a poll-anytime property
    (a caller samples it once per work loop, same as ANCHOR_OUTBOX_PENDING/
    INDEXER_POLL_LAG_BLOCKS), so there's no reason this module needs to own
    a Gauge update for it. RPC_CALL_FAILURES_TOTAL (make_request's own
    except block, above) is the one deliberate exception — a failure EVENT
    isn't a poll-anytime property the way state is; the only place that
    ever knows a specific call failed is make_request itself, so that
    counter increment has to live here, not in a caller that never sees
    individual call outcomes."""
    provider = w3.provider
    if not isinstance(provider, FallbackHTTPProvider):
        return {}
    return {url: breaker.state for url, breaker in zip(provider.endpoint_urls, provider.breakers)}
