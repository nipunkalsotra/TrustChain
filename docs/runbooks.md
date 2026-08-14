# Runbooks

One section per alert defined in `docker/prometheus/alerts.yml` — each
alert's `runbook` annotation links to the matching `#anchor` here. Written
for whoever's on call, not whoever wrote the code: assume you're reading
this at 2am with no other context.

Dashboards: Grafana at `:3002` (local dev — `docker compose up grafana`),
dashboard "TrustChain". Metrics: `docker/prometheus/prometheus.yml`'s
scrape targets, defined in `backend/observability.py`. Logs: structured
JSON via `structlog` (`backend/logging_config.py`) — every line carries
`request_id`, and pipeline-related lines also carry `run_id`; grep/filter
on those to follow one request or run through everything it caused.

---

## High HTTP 5xx rate

**Fires when:** more than 5% of API responses over 5 minutes are 5xx.

1. Check `GET /health` and `GET /ready` directly — if `/ready` is failing,
   the database is the likely root cause (it's the only hard dependency
   `/ready` checks); if only `/health` is degraded, look at `/chain-status`
   for an RPC-side issue instead.
2. Grep API logs for `level=error` in the affected window; structured
   fields (`request_id`, `error`) point at the failing endpoint and
   exception directly.
3. Common causes: Postgres connection exhaustion (check active connection
   count against the pool size), Redis unavailability (rate
   limiting/SSE/idempotency all fail open or degrade gracefully on their
   own — a genuine Redis outage shouldn't itself cause 5xxs; if it is,
   that's a bug, not expected behavior), or an unhandled exception in a
   new code path.
4. If it's isolated to `POST /run-agent` or `/stream/{run_id}`: check the
   LLM provider (Groq) and MCP servers (`mcp-search`, `mcp-blockchain`)
   are reachable — `docker compose logs mcp-search mcp-blockchain`.

## Pipeline failure rate high

**Fires when:** more than 20% of agent pipeline runs (`pipeline_runs_total`)
end in `status=failed` over 15 minutes.

1. Pick one failed run's `run_id` from logs (`pipeline_background_error`
   log lines carry it) and read every log line for that `run_id` — the
   correlation exists exactly for this.
2. Failures raise from `agents/scorer.py`'s structured-output validation
   (F18 — a malformed LLM response fails loudly instead of silently
   defaulting to score=50) more often than anywhere else; check whether
   the LLM provider is degraded (Groq status page) or a prompt change
   broke structured-output compliance.
3. If failures cluster around one specific `task` input pattern rather
   than being spread evenly, it's more likely a prompt/parsing bug than
   an infra issue.

## Rate limit rejections spike

**Fires when:** more than 100 rate-limit rejections (`kind=run_agent` or
`kind=login`) in 5 minutes. Severity `info` — this is often just
legitimate traffic or a client retry-storm, not an incident by itself.

1. Check the `kind` label (Grafana panel "Rate limit rejections", split
   by kind, or query `rate_limit_rejections_total` directly).
2. `kind=login`: likely credential stuffing (`rate_limit.py`'s login
   backoff — see `check_login_backoff`) — if failures cluster on very few
   accounts from many IPs, or one IP against many accounts, that's the
   defense working as intended, not a bug to fix.
3. `kind=run_agent`: check whether one project is responsible (project-
   scoped bucket — see `config.run_agent_rate_limit_capacity`) via
   `POST /run-agent` request logs' `project_id` field, vs. a broad spike
   across many projects (more likely a real traffic increase — consider
   raising `RUN_AGENT_RATE_LIMIT_CAPACITY`/`_REFILL_PER_SECOND`).

## Anchor outbox backlog growing

**Fires when:** `anchor_outbox_pending` (steps recorded but not yet
anchored on-chain — see `agents/base.py`'s transactional-outbox pattern)
stays above 500 for 10+ minutes.

1. Is the anchor worker process alive? `docker compose ps anchor-worker`
   / check its `/metrics` endpoint (`:9101`) is even reachable — an
   unreachable target in Prometheus (`up{job="trustchain-anchor-worker"}
   == 0`) means the process is down, not just behind.
2. If it's alive but not making progress: check its logs for
   `batch_submit_failed` or `anchor_worker_iteration_failed` — a stuck
   nonce, an RPC outage, or a wallet out of gas are the likely causes.
3. Check `anchor_batches_failed_total` by `reason` — `revert` usually
   means a genuine on-chain issue (wrong role, paused contract);
   `timeout` usually means the RPC endpoint or chain itself is slow;
   `build_or_send` usually means a local signing/RPC-connectivity
   problem (see `blockchain/signer.py` if SIGNER_BACKEND=aws_kms/gcp_kms
   — a KMS outage or permissions change surfaces here).
4. The reaper (`anchor_worker/reaper.py`) recovers stale claims after
   `ANCHOR_CLAIM_TIMEOUT_SECONDS` on its own — a backlog that keeps
   growing despite an apparently-healthy worker process for longer than
   that timeout is the strongest signal something is actually stuck
   rather than just catching up from a burst.

## Anchor batches failing repeatedly

**Fires when:** more than 5 anchor batch failures in 15 minutes.
Severity `critical` — unlike a growing backlog (which can be "just slow"),
repeated failures mean batches are actively being rejected or timing out.

1. Same triage as above, `reason` label first.
2. `reason=revert`: check the anchor worker's wallet still holds
   `ANCHOR_ROLE` on `AgentAuditLogV2` — a `TransferAdminToMultisig` run
   (see `docs/multisig-admin-handoff.md`) only touches `DEFAULT_ADMIN_ROLE`
   and shouldn't affect this, but a manual role change would.
3. Failed batches' steps are automatically detached and requeued (or
   dead-lettered past `ANCHOR_MAX_ATTEMPTS` —
   `anchor_worker/main.py::handle_submit_failure`) — check for
   `status='dead_letter'` rows in `anchor_outbox` directly if the metric
   stays elevated after the underlying cause is fixed; those need a
   manual `UPDATE ... SET status='pending', attempts=0` after confirming
   the root cause is actually resolved, or they'll just fail again.

## Anchor wallet balance low

**Fires when:** `anchor_wallet_balance_wei` (the anchor worker's signing
wallet, sampled once per work loop — `anchor_worker/main.py::run_once`)
drops below 0.05 native token for 5+ minutes. Severity `critical` — this
is the "gas exhaustion" chaos scenario (see `tests/test_chaos.py`'s
insufficient-funds test): once the balance hits zero, every `anchorBatch`
call fails at the pre-send stage (`submit.py`'s `reason=build_or_send`,
"insufficient funds for gas * price + value") rather than reverting
on-chain, so `AnchorBatchesFailingRepeatedly` may lag behind this one or
not fire cleanly at all — the transaction never gets far enough to
produce a receipt.

1. Check the current balance directly: `cast balance <address> --rpc-url
   <RPC_URL>`, address = the wallet `anchor_worker/chain.py::get_signer()`
   resolves for the configured `SIGNER_BACKEND`.
2. Send more native token to that address. On Monad testnet, use the
   faucet; in a `vault_kv`/KMS setup the address doesn't change on
   rotation, only the signing material does, so funding is a one-time
   concern per address, not per key rotation.
3. Once funded, `anchor_wallet_balance_wei` recovers on the next work
   loop (no restart needed) — confirm the alert clears and
   `anchor_outbox_pending` (which will have been growing the whole time
   this was silently failing) starts draining.

## RPC circuit breaker open

**Fires when:** `rpc_circuit_breaker_open{endpoint=...}` has been `1` for
5+ minutes — only emitted at all when `MONAD_RPC_FALLBACK_URLS` or
`V2_RPC_FALLBACK_URLS` configures more than one endpoint (see
`blockchain/resilient_provider.py`). Severity `warning`, not `critical`:
a single open breaker means traffic is riding on a fallback endpoint
successfully, not that anything is actually down yet.

1. Which endpoint tripped? The `endpoint` label is the literal URL.
   Check that provider's own status page/dashboard — this almost always
   means the RPC provider itself is degraded, not a TrustChain bug.
2. Confirm the fallback is actually absorbing traffic cleanly: anchor
   worker/indexer logs shouldn't show `anchor_worker_iteration_failed`/
   `indexer_iteration_failed` during this window if failover is working.
3. The breaker self-heals — it moves to `half_open` and retries the
   original endpoint automatically once its cooldown elapses (default
   30s per `CircuitBreaker.reset_timeout_seconds`); nothing to
   manually reset. If it keeps reopening, the endpoint is still bad.
4. If EVERY configured endpoint's breaker opens at once, that's a real
   outage, not graceful degradation — check `AnchorBatchesFailingRepeatedly`
   / `IndexerFallingBehind`, which will fire from the underlying RPC
   failures regardless of this alert.

## Indexer falling behind

**Fires when:** `indexer_poll_lag_blocks` (chain head minus the indexer's
last-processed block) exceeds 1000 for 10+ minutes.

1. Is the indexer process alive? Same check as the anchor worker —
   `up{job="trustchain-indexer"}` in Prometheus, `docker compose ps indexer`.
2. If alive: check its logs for `indexer_iteration_failed` — an RPC
   endpoint returning errors or an extremely large event backlog
   (`event.get_logs` over a huge block range can be slow/rate-limited by
   the RPC provider) are the usual causes.
3. Falling behind on `TrustScoreRegistryV2.ScoreUpdated` delays
   `/trust-scores` and `/leaderboard` results from reflecting reality;
   falling behind on `AgentAuditLogV2.BatchAnchored` delays
   `indexer/reconcile.py`'s crash-recovery path (see
   `indexer_reconciliations_total` — if the anchor worker crashed while a
   batch was mid-confirmation during this lag window, that batch stays
   at `status='submitted'` in Postgres, even though it may have already
   succeeded on-chain, until the indexer catches back up).
