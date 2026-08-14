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

A single-endpoint config (today's default — no *_RPC_FALLBACK_URLS set)
makes FallbackHTTPProvider behave identically to a plain HTTPProvider:
one endpoint, no breaker state ever reached (a single failure just raises,
same as before), zero behavior change for anyone who hasn't opted in.
"""

import threading
import time
from typing import Any, Optional

from web3 import HTTPProvider
from web3.types import RPCEndpoint, RPCResponse


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
    ):
        if not endpoint_urls:
            raise ValueError("FallbackHTTPProvider needs at least one endpoint URL")
        super().__init__(endpoint_urls[0], request_kwargs=request_kwargs)
        self.endpoint_urls = list(endpoint_urls)
        self._providers = [HTTPProvider(url, request_kwargs=request_kwargs) for url in endpoint_urls]
        self.breakers = [CircuitBreaker(failure_threshold, reset_timeout_seconds) for _ in endpoint_urls]

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        last_error: Optional[Exception] = None
        attempted_any = False

        for provider, breaker in zip(self._providers, self.breakers):
            if not breaker.allow_request():
                continue
            attempted_any = True
            try:
                response = provider.make_request(method, params)
            except Exception as e:  # noqa: BLE001 — any RPC-layer failure triggers failover
                breaker.record_failure()
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
    than touching Prometheus directly — this module has no metrics
    dependency; callers (anchor_worker/main.py, indexer/main.py) own
    turning this into their own Gauge updates, same as how they already
    sample ANCHOR_OUTBOX_PENDING/INDEXER_POLL_LAG_BLOCKS themselves."""
    provider = w3.provider
    if not isinstance(provider, FallbackHTTPProvider):
        return {}
    return {url: breaker.state for url, breaker in zip(provider.endpoint_urls, provider.breakers)}
