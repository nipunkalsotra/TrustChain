# 0009 — RPC fallback + circuit breaker

**Status:** Accepted

## Context

Every RPC call in the codebase went through a single
`Web3(HTTPProvider(one_url))`. A transient RPC outage (the testnet node
restarting, a rate limit, a network blip) took down anchoring, scoring,
and health checks with no recovery path except a process restart once
the endpoint came back — real production risk for a chain client, not a
hypothetical.

## Decision

`blockchain/resilient_provider.py`'s `FallbackHTTPProvider` — a web3.py
`Provider` that tries a configured list of endpoint URLs in order, each
with its own `CircuitBreaker` (CLOSED → OPEN after N consecutive
failures → HALF_OPEN after a cooldown → CLOSED again on success). An
endpoint whose breaker is OPEN is **skipped entirely**, not retried and
timed out on every call — the difference between a bare "try primary,
catch, try fallback" retry loop (which still pays the dead primary's
full timeout on *every* request, forever, until it recovers) and this
(a dead endpoint stops costing anything until its cooldown elapses).
Configured via `MONAD_RPC_FALLBACK_URLS`/`V2_RPC_FALLBACK_URLS`
(comma-separated); empty (the default) makes `build_w3()` construct a
plain single-endpoint `HTTPProvider`, byte-for-byte the old behavior —
this is opt-in, not a forced behavior change.

Circuit-breaker state is exposed to Prometheus
(`rpc_circuit_breaker_open{endpoint=...}`, sampled once per anchor-worker/
indexer poll loop) with a paired alert
(`RpcCircuitBreakerOpen` in `docker/prometheus/alerts.yml`) — a breaker
that's been open for 5+ minutes is worth knowing about even though
traffic is still flowing through the fallback.

## Alternatives considered

- **A bare retry loop with no breaker (try each URL every time).**
  Rejected: doesn't solve the actual production failure mode — a
  consistently-dead primary still adds its full connection/read timeout
  to every single request forever, not just the first one.
- **A third-party circuit-breaker library** (e.g. `pybreaker`).
  Rejected: the actual state machine needed here is small (closed/open/
  half-open, per-endpoint) and implementing it directly avoids an
  extra dependency for a few dozen lines of well-tested logic
  (`tests/test_resilient_provider.py` covers it against a real dead
  port and real Anvil, not mocks).
- **Fail over at the DNS/load-balancer level instead of in application
  code.** Rejected: this project has no such infrastructure layer to
  put it in, and application-level failover is directly testable
  (`tests/test_resilient_provider.py`, `tests/test_chaos.py`) in a way
  an external LB config isn't, from inside this repo.

## Consequences

- A real bug was caught building this: the circuit breaker's
  `record_failure()` originally only set `_opened_at` the *first* time
  the threshold was crossed — a failed HALF_OPEN trial (the one retry
  allowed after cooldown) left the stale timestamp in place, so `state`
  kept reporting `half_open` forever instead of correctly reopening for
  a fresh cooldown. Fixed by refreshing `_opened_at` on every failure at
  or above threshold, found by `tests/test_resilient_provider.py`'s own
  half-open-retry-fails test, not by inspection.
- `sample_breaker_states()` is a pure, Prometheus-agnostic function
  (`blockchain/resilient_provider.py`) — the module has no metrics
  dependency; anchor-worker/indexer each own turning its output into
  their own Gauge updates, matching how they already self-sample
  `anchor_outbox_pending`/`indexer_poll_lag_blocks`.
- Multi-endpoint configuration is currently unused in this project's own
  deployment (no fallback URLs configured anywhere by default) — the
  mechanism is built and tested, but its real value is only realized
  once an operator actually configures a second RPC provider.
