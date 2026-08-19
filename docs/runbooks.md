# Runbooks

Two kinds of section below. **Alert runbooks** — one per alert defined in
`docker/prometheus/alerts.yml`, each alert's `runbook` annotation links to
the matching `#anchor` here — are reactive: something fired, follow the
steps. **Operational procedures** are proactive: rehearsed steps for a
planned or emergency action nothing pages you for on its own (rotating a
key, pausing a contract, restoring a backup). Written for whoever's on
call, not whoever wrote the code: assume you're reading this at 2am with
no other context.

Dashboards: Grafana at `:3002` (local dev — `docker compose up grafana`),
dashboards "TrustChain" (core service health) and "TrustChain — Integrity
& Alerting" (Phase 3 — watchdog sweep/cursor state, alert-raising and
-delivery, membership/invitation activity). Metrics: `docker/prometheus/prometheus.yml`'s
scrape targets, defined in `backend/observability.py`. Logs: structured
JSON via `structlog` (`backend/logging_config.py`) — every line carries
`request_id`, and pipeline-related lines also carry `run_id`; grep/filter
on those to follow one request or run through everything it caused.

For *why* each alert's threshold is what it is (not just what to do when
it fires) — the SLO/error-budget policy tying them together as a set,
rather than a pile of independently-tuned numbers — see
[`docs/slo.md`](slo.md).

---

# Alert runbooks

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

## Anchor outbox steps dead-lettered

**Fires when:** `anchor_outbox_dead_lettered_total` increases at all in a
5-minute window — `for: 0m`, not a sustained-condition alert like the
others above. Severity `critical`, unconditionally: a dead-lettered step
is permanent — it will never be anchored on-chain without manual
intervention, breaking this product's core guarantee (every agent step
gets an immutable, verifiable audit trail) for exactly that step. Two
distinct `source` label values, from two distinct code paths:

- `source="reaper"` (`anchor_worker/reaper.py`): a claim outlived
  `anchor_claim_timeout_seconds` AND had already exhausted
  `anchor_max_attempts` — almost always means the worker crashed/restarted
  repeatedly while holding the same row (check for `anchor_worker_starting`
  log lines close together — a genuine crash loop, not one clean restart).
- `source="submit_failure"` (`anchor_worker/main.py::handle_submit_failure`):
  a batch's on-chain submission itself (revert or confirmation timeout)
  failed `anchor_max_attempts` times — see "Anchor batches failing
  repeatedly" above for what usually causes a revert/timeout in the first
  place; this fires once THAT keeps happening long enough to exhaust
  retries for a specific batch's steps.

1. Find the affected rows: `SELECT id, step_id, attempts, last_error FROM
   anchor_outbox WHERE status = 'dead_letter' ORDER BY id DESC LIMIT 50;`
   — `last_error` holds the final failure reason from whichever path
   dead-lettered it.
2. Root-cause via `last_error` and the two `source` cases above — do NOT
   just requeue before understanding why, or the same rows dead-letter
   again on the exact same underlying problem.
3. Once the root cause is fixed, recovery is manual (deliberately no
   automatic un-dead-lettering — see "Anchor batches failing repeatedly"'s
   step 3 for the exact `UPDATE`): reset `status='pending', attempts=0,
   claimed_by=NULL, claimed_at=NULL, batch_id=NULL` for the affected rows,
   confirm they anchor cleanly on the next few work loops, and confirm
   `anchor_outbox_dead_lettered_total`'s rate returns to zero.
4. If rows were dead-lettered for a reason that can't be fixed after the
   fact (e.g. a step whose content was somehow lost, not just delayed),
   this IS a real, permanent audit-trail gap for that step — document it
   rather than silently requeuing something that will just fail again or
   masking that the guarantee was actually broken for that data.

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

## Token budget ceiling breached

**Fires when:** `token_budget_rejections_total{org_id=...}` increases at
all in a 15-minute window. Severity `warning` — this is expected,
correct behavior (`POST /run-agent` returning 429 once an org's
`organizations.token_budget` is reached — see `db/tenancy.py`'s
`get_org_token_budget_status`/`record_token_spend`), not a bug. The
alert exists so an operator notices and acts, not because anything is
broken.

1. Identify the org from the `org_id` label, or query that org's own
   `GET /gas-spend` response (`orgTokenBudget`/`orgTokensSpent` fields)
   for the exact numbers.
2. Decide whether to raise the ceiling: `UPDATE organizations SET
   token_budget = <new value> WHERE id = '<org_id>';` — `NULL` means
   unlimited (see the column's own comment in `db/models.py`).
3. No restart or replay needed — the next `POST /run-agent` from that
   org re-checks the (now higher) budget immediately; nothing was lost,
   requests were rejected pre-flight before any LLM spend happened.

## Anchor reaper resets crash loop

**Fires when:** `anchor_reaper_reset_total` increases by more than 20 in
a 15-minute window. Severity `warning`. A reaper reset on its own is
normal — `anchor_worker/reaper.py` reclaims a claim that outlived
`anchor_claim_timeout_seconds` back to `pending`, no data lost, and one
or two of these after an ordinary restart is expected. A high rate
means the worker is dying mid-claim repeatedly, not recovering cleanly.

1. Check for repeated `anchor_worker_starting` log lines close together
   — that's the crash loop itself, not just this alert's symptom.
2. Look at what's between each restart: an unhandled exception in
   `anchor_worker/main.py::run_once`, an OOM kill (`docker compose ps`/
   `docker stats anchor-worker`), or a supervisor (systemd/docker
   restart policy) retrying a container that's failing at startup
   (bad `SIGNER_BACKEND` config, unreachable RPC, unreachable Postgres).
3. This alert firing without `AnchorOutboxStepsDeadLettered` also firing
   means no permanent damage yet — rows keep getting reclaimed and
   retried — but the underlying instability should still be fixed before
   attempts on those rows eventually exhaust `anchor_max_attempts` and
   they dead-letter for real.

## Anchor gas ceiling breached

**Fires when:** `anchor_gas_ceiling_breached_total{org_id=...}`
increases at all in a 15-minute window. Severity `warning` — like the
token budget alert above, this is expected behavior
(`anchor_worker/main.py` skipping a batch back into the outbox, not
losing it, once `organizations.gas_spent_wei` would exceed
`organizations.gas_budget_wei` — see `db/tenancy.py`'s
`get_org_gas_budget_status`/`record_gas_spend`), an operator-attention
signal rather than an incident.

1. Identify the org from the `org_id` label, or that org's `GET
   /gas-spend` response (`gasBudgetWei`/`gasSpentWei`).
2. Decide whether to raise the ceiling: `UPDATE organizations SET
   gas_budget_wei = <new value> WHERE id = '<org_id>';` (`NULL` =
   unlimited).
3. Once raised, that org's steps (still sitting in `anchor_outbox` as
   `status='pending'`, never touched) anchor normally on the anchor
   worker's next work loop — no manual requeue needed, nothing was lost.
4. If the ceiling was hit unexpectedly (not a deliberate cap), check
   `anchor_batch_gas_cost_wei`/`anchor_gas_price_wei` for that org's
   recent batches — a spike could mean gas prices rose on-chain, or a
   batch size/frequency change is spending faster than expected.

## Agent integrity violations detected

**Fires when:** `agent_integrity_violations_total` increases by more
than 3 in a 15-minute window. Severity `warning`. A single violation
(e.g. a caller probing `verifyAgent` with mismatched data) isn't itself
an incident; this alert's threshold is deliberately the same "fires
unexpectedly at scale" condition "Pausing contracts in an emergency"
above names as one of its own trigger conditions.

1. Check the indexer's logs / `audit_events` around the firing window
   for which agent(s) and project(s) are involved
   (`indexer/agent_events.py::index_integrity_violation`).
2. Distinguish a legitimate one-off (a caller's own bug, or a genuine
   tamper attempt against one agent) from a pattern suggesting a
   compromised or misbehaving actor at scale across many agents/projects.
3. If it looks like an active, ongoing attempt to submit forged agent
   data rather than an isolated incident, follow "Pausing contracts in
   an emergency" above — pause `AgentAuditLogV2`/`TrustScoreRegistryV2`
   while investigating.

## RPC call failures elevated

**Fires when:** `rpc_call_failures_total` (summed across all configured
endpoints) increases by more than 10 in a 15-minute window. Severity
`warning`.

1. Check whether `RpcCircuitBreakerOpen` is also firing (same
   underlying `blockchain/resilient_provider.py` code path, per
   `endpoint`). If yes, follow that runbook entry above — failures are
   already concentrated enough on one endpoint to have tripped its
   breaker and failover is (or should be) absorbing them.
2. If this fires WITHOUT `RpcCircuitBreakerOpen`: failures are either
   spread thinly enough across endpoints that none individually crossed
   its breaker's failure threshold, or `MONAD_RPC_FALLBACK_URLS`/
   `V2_RPC_FALLBACK_URLS` only configures a single endpoint (no breaker
   metric is emitted at all in that case — see the alert's own
   description in `alerts.yml`). Either way, check anchor-worker/indexer
   logs for elevated `anchor_worker_iteration_failed`/
   `indexer_iteration_failed` rates to gauge real user-facing impact.
3. This is usually the RPC provider's own degradation, not a TrustChain
   bug — check the provider's status page first.

## TrustChain Integrity Mismatch

**Fires when:** `integrity_checks_total{result="mismatch"}` increases at
all in a 5-minute window — no `for:` delay, pages immediately. Severity
`critical`. This is the actual "did TrustChain's audit trail stop
matching what it anchored" alarm (Phase 3 §6) — a real tamper finding
from one of the integrity watchdog's detectors
(`backend/integrity_watchdog/detectors/`), not a liveness/performance
signal like most of the alerts above.

1. Check `GET /alerts?severity=critical` (or `trustchain alerts list
   --severity critical`) for the specific alert row — every firing of
   this rule should correspond to exactly one, with full evidence
   (expected vs. actual hash, step/batch id, tx hash, block number where
   applicable) in its `evidence` field. **Do not treat the Prometheus
   alert alone as actionable** — the `alerts` row is what a human
   actually investigates from.
2. `detector` label on the metric tells you which one fired:
   `step_rows` (a step's stored `leaf_hash` no longer matches its own
   content — someone edited a row directly), `merkle_roots_rebuild` (the
   batch no longer rebuilds to its recorded root — a more sophisticated
   edit that also touched `leaf_hash`), or `merkle_roots_onchain` (the
   database's own `merkle_root` disagrees with what
   `AgentAuditLogV2.getBatch()` returns on-chain — the strongest
   signal; this one means either full Postgres compromise or a genuine
   on-chain anomaly, not just an application bug).
3. Confirm scope: is this ONE step/batch (contained — possibly a bug in
   a specific write path, or a targeted tamper attempt) or MANY across
   different projects/orgs in the same window (suggests broader database
   compromise — treat as a security incident, not a data-quality one;
   consider "Pausing contracts in an emergency" above if on-chain writes
   are also in question).
4. For a `merkle_roots_onchain` mismatch specifically: verify independently
   with `cast call` against the real `AgentAuditLogV2` contract's
   `getBatch(anchorId)` — do not trust the watchdog's own report of what
   the chain said without a second, manual read, since a compromised
   watchdog process is exactly the failure mode `TrustChainWatchdogSilent`
   below exists to catch, but a compromised watchdog that keeps running
   while lying about individual findings is a different, worse case this
   manual check guards against.
5. This is a genuine security incident, not routine ops — involve
   whoever owns incident response before taking any destructive action
   (e.g. don't `resolve` the alert until root cause is understood; a
   resolved alert frees its dedupe key, and a premature resolve just
   means the SAME problem re-alerts as if new on the next sweep instead
   of preserving `occurrence_count` history).

## TrustChain Watchdog Silent

**Fires when:** `time() - max(watchdog_last_success_timestamp) > 600` —
no detector has completed a successful cycle in 10+ minutes. Severity
`critical`. This is the "who watches the watchmen" rule (Phase 3 §6.8's
stated residual risk): a suppressed or crashed watchdog raises zero
alerts, which is indistinguishable from "nothing is wrong" by every
other metric in this file.

1. Check whether the `integrity-watchdog` container/process is running
   at all (`docker compose ps integrity-watchdog`) — most common cause
   is simply that it crashed or was never started.
2. If it's running but not progressing: check its logs for
   `integrity_watchdog_cycle_failed` (an exception inside `run_cycle`,
   `backend/integrity_watchdog/main.py`) — a single detector's exception
   is caught per-cycle, so a persistent failure here means something
   structural (e.g. a bad DB migration state, or `watchdog_cursor`
   corrupted) rather than a transient blip.
3. Check `pg_stat_activity` for a stuck advisory lock
   (`backend/integrity_watchdog/lock.py`,
   `config.watchdog_advisory_lock_key`) — a crashed instance that didn't
   cleanly close its connection can, in rare cases, leave the lock held
   until Postgres notices the connection is dead; a replacement instance
   will otherwise sit in `integrity_watchdog_waiting_for_lock` forever.
4. Restart the service once the underlying cause is fixed
   (`docker compose restart integrity-watchdog`) — no state to reconcile
   beyond what `watchdog_cursor` already tracks; a restart just resumes
   the rolling tier from its last persisted position.

## TrustChain Full Sweep Stale

**Fires when:** `watchdog_full_sweep_age_seconds > 86400` for 10+
minutes. Severity `warning`. Coverage is still happening (the rolling
tier hasn't stopped, see `TrustChainWatchdogSilent` for that failure
mode) — it's just taking longer than the informational target
(`config.watchdog_full_sweep_target_seconds`, default 6h) to complete
one full pass over all history.

1. Not urgent by itself — this means detection LATENCY for older,
   previously-unswept history has degraded, not that anything was
   missed for data already covered by a completed pass.
2. Check `GET /integrity/status` for `stepsVerified`/`batchesVerified`
   growth relative to history size — if step/batch volume has grown
   substantially, this is expected: `watchdog_rolling_steps_per_cycle`/
   `watchdog_rolling_batches_per_cycle` (the fixed per-cycle budget,
   Phase 3 §6.7/ADR-0015) intentionally does NOT scale with history size,
   so a full pass takes proportionally longer as the system succeeds and
   accumulates more data.
3. If genuinely falling behind and faster coverage matters more than the
   per-cycle cost tradeoff, raise `watchdog_rolling_steps_per_cycle`/
   `watchdog_rolling_batches_per_cycle` (and/or lower
   `watchdog_poll_interval_seconds`) — this trades more DB/RPC load per
   cycle for a shorter full-pass duration.

## TrustChain Alert Delivery Backlog

**Fires when:** `alert_delivery_queue_depth > 100` for 10+ minutes.
Severity `warning`. Pending/claimed `alert_deliveries` rows are piling
up faster than `notifications/sender.py`'s drain loop is clearing them.

1. Check `integrity-watchdog`'s logs (the sender loop runs in-process
   there, see `integrity_watchdog/main.py`) for
   `integrity_watchdog_sender_iteration_failed` — an exception in
   `notifications/sender.py::run_once` would stall the whole drain loop,
   not just one delivery.
2. Check whether `TrustChainAlertDeliveryFailing` (below) is ALSO
   firing — if so, the backlog is a symptom of the send failures, not an
   independent problem; fix that first.
3. If neither: this may just be a genuine burst (e.g. the first full
   watchdog sweep after rollout, or a real incident generating many
   alerts at once — see Phase 3 plan §16's rollout warning about the
   first sweep). Confirm the queue is actually DRAINING over time
   (`alert_delivery_queue_depth` trending down), not stuck — a draining-
   but-large backlog will clear on its own; a flat one will not.

## TrustChain Alert Delivery Failing

**Fires when:** `rate(alert_deliveries_total{status="failed"}[15m]) >
0.1` for 10+ minutes. Severity `critical` — deliberately higher than
the backlog rule above, because a failure to deliver an alert means
owners/admins are NOT being told about whatever the underlying alert
actually was, on top of whatever that alert itself already warranted.

1. Check `config.email_backend` (`console`/`smtp`/`ses`/`memory`) and
   that backend's specific prerequisites:
   - `smtp`: `smtp_host`/`smtp_username`/`smtp_password` reachable and
     correct (`notifications/backends/smtp.py`).
   - `ses`: IAM role/credentials valid, sending identity still verified,
     NOT still sandbox-restricted to only verified recipient addresses
     (`notifications/backends/ses.py`'s own docstring — this is a real,
     easy-to-hit cause: a domain that was verified once can still reject
     sends to unverified recipients until SES production access is
     granted).
2. Check `alert_deliveries.last_error` for the specific failed rows
   (`SELECT last_error FROM alert_deliveries WHERE status IN
   ('failed','dead_letter') ORDER BY id DESC LIMIT 20`) — the exact
   provider error (auth failure, rate limit, bounced/rejected recipient)
   determines the fix.
3. Deliveries that exhausted `alert_delivery_max_attempts` are
   `dead_letter`, not `failed`/retrying — those need manual intervention
   (fix the underlying cause, then either wait for the NEXT occurrence
   of that alert to generate a fresh delivery, or re-queue by hand:
   `UPDATE alert_deliveries SET status='pending', attempts=0,
   next_attempt_at=<now> WHERE id = ...`).
4. This is the one alert-on-alerting failure — if the underlying cause
   can't be fixed quickly, manually check `GET /alerts` for what's been
   missed and notify the affected org(s) through a side channel in the
   meantime.

---

# Operational procedures

## Rotating the anchor worker's signing key

**When:** scheduled key rotation, suspected key compromise, or moving a
deployment from `local` to a KMS-backed `SIGNER_BACKEND` for the first
time. The anchor worker's key holds `ANCHOR_ROLE` on `AgentAuditLogV2` and
`TrustScoreRegistryV2` (granted by `DeployV2.s.sol` at deploy time) —
that's the only privilege it has (it cannot pause, register/revoke
agents, or reset runs; see `docs/multisig-admin-handoff.md` for those),
but it's a hot, automated key that signs real transactions on every work
loop, which is exactly why rotating it without downtime or a stuck outbox
needs care.

This is about replacing the actual signing key material — distinct from
what "Anchor wallet balance low" above means by "rotation" in its funding
note (rotating Vault/KMS *access credentials*, e.g. a Vault token or IAM
key, without touching the secp256k1 key those credentials protect, which
indeed leaves the address unchanged and doesn't need any of this). The
address a REAL key-material rotation produces depends on `SIGNER_BACKEND`
(`backend/blockchain/signer.py`):

- `local` / `vault_kv`: the signing address is derived from the raw
  private key, so a **new key means a new address** — the grant/cutover/
  revoke dance below is required every time.
- `aws_kms` / `gcp_kms`: AWS KMS does not support automatic rotation for
  asymmetric keys at all (only symmetric keys can auto-rotate) — rotating
  a KMS-backed signer always means creating a brand new asymmetric key
  and pointing `KMS_KEY_ID` at it, which produces a **new address**, same
  as the local/vault_kv case. Confirm the actual address either way with
  `signer.address` (or `GET /chain-status`, which reports the currently
  configured wallet) rather than assuming.

Steps (all four backends share this shape once you have the new
address):

1. Determine the new signer's address ahead of time without touching the
   live deployment: `local`/`vault_kv` — derive it from the new key
   locally (`python3 -c "from eth_account import Account; print(Account.from_key('0x...').address)"`);
   `aws_kms`/`gcp_kms` — create the new key/version first, then read its
   address via `AwsKmsSigner(...).address` / `GcpKmsSigner(...).address`
   (or the provider console's public-key export) before it's live
   anywhere.
2. Grant `ANCHOR_ROLE` to the new address on both contracts, from the
   current `DEFAULT_ADMIN_ROLE` holder (a single EOA in local dev; a
   Gnosis Safe if `docs/multisig-admin-handoff.md`'s handoff has already
   run, in which case this grant goes through the Safe's own signing
   flow instead of a bare `cast send`):

   ```bash
   cast send <AgentAuditLogV2 address> \
     "grantRole(bytes32,address)" \
     "$(cast keccak "ANCHOR_ROLE")" <new signer address> \
     --rpc-url <rpc> --private-key <current admin key>

   cast send <TrustScoreRegistryV2 address> \
     "grantRole(bytes32,address)" \
     "$(cast keccak "ANCHOR_ROLE")" <new signer address> \
     --rpc-url <rpc> --private-key <current admin key>
   ```

3. Verify the grant landed on-chain before touching the running worker:
   `cast call <address> "hasRole(bytes32,address)(bool)" "$(cast keccak "ANCHOR_ROLE")" <new signer address> --rpc-url <rpc>`
   must return `true` on both contracts.
4. Fund the new address with enough native token to operate — see
   "Anchor wallet balance low" above for how much and how to check; an
   unfunded new key just moves the gas-exhaustion failure mode to a
   different address.
5. Update the anchor worker's own configuration to the new signing
   material and restart it: `PRIVATE_KEY`/`V2_PRIVATE_KEY` for `local`,
   `VAULT_SECRET_PATH` (pointing at a KV entry already updated with the
   new key) for `vault_kv`, or `KMS_KEY_ID` for `aws_kms`/`gcp_kms`. Then
   `docker compose up -d --build anchor-worker` (or redeploy the process
   in a non-Compose environment) — see this doc's opening note on
   Compose recreating `anvil` on any `--build` in **local dev only**;
   this does not apply to a real deployment against a persistent chain.
6. Confirm the cutover: `GET /chain-status` (or the worker's own startup
   logs) should report the new address; watch `anchor_batches_submitted_total`
   /`anchor_outbox_pending` for a few successful work loops — a batch
   actually confirming on-chain with the new key is the real proof this
   worked, not just "the process started without erroring."
7. Only once step 6 has shown several confirmed batches, revoke the role
   from the OLD address on both contracts (same `cast send` shape as
   step 2, with `revokeRole` in place of `grantRole`) and verify via
   `hasRole` returns `false`. Revoking before confirming a working
   cutover risks the exact same "stuck outbox, no valid signer" failure
   this rotation was trying to avoid causing.

## Pausing contracts in an emergency

**When:** an `IntegrityViolation` event fires unexpectedly at scale, a
contract bug is discovered post-deployment, or the anchor worker's key is
suspected compromised and revoking `ANCHOR_ROLE` (see rotation above)
isn't fast enough on its own. Only `AgentAuditLogV2` and
`TrustScoreRegistryV2` are `Pausable` — `AgentIdentityRegistryV2` and
`TrustChainRegistry` have no pause switch, by design (see each
contract's own docstring for why); an incident involving those two
requires revoking the relevant role instead (`REGISTRAR_ROLE` for
`AgentIdentityRegistryV2`, `ANCHOR_ROLE`/`DEFAULT_ADMIN_ROLE` more
generally).

`pause()`/`unpause()` both require `DEFAULT_ADMIN_ROLE` — a single EOA in
local dev, a Gnosis Safe if `docs/multisig-admin-handoff.md`'s handoff has
run (in which case this needs a Safe signature round, which is slower;
weigh that against the severity of what you're pausing for when deciding
whether to pause at all).

1. Pause the affected contract(s):

   ```bash
   cast send <AgentAuditLogV2 or TrustScoreRegistryV2 address> "pause()" \
     --rpc-url <rpc> --private-key <admin key, or via Safe>
   ```

2. Confirm it took effect: `cast call <address> "paused()(bool)" --rpc-url <rpc>`
   must return `true`. While paused, `anchorBatch`/`updateScore`/
   `updateScoresBatch` all revert — the anchor worker will keep retrying
   and its outbox backlog will grow (expected; see "Anchor outbox backlog
   growing" above — that alert will fire during a deliberate pause, which
   is normal, not a second incident).
3. Investigate and fix the root cause with the contract paused — this is
   the point of pausing: buying time without further on-chain state
   changes while the actual problem gets diagnosed.
4. Unpause only once the root cause is actually resolved (a code fix
   alone doesn't retroactively fix an already-deployed contract — pausing
   a bug doesn't un-deploy it; a genuine contract bug needs a new
   deployment and cutover, not just an unpause):

   ```bash
   cast send <address> "unpause()" --rpc-url <rpc> --private-key <admin key, or via Safe>
   ```

5. Confirm `paused()` returns `false`, then watch `anchor_outbox_pending`
   drain and `anchor_batches_submitted_total` resume increasing — the
   backlog built up during the pause should clear on its own; the anchor
   worker doesn't need a restart, it just keeps retrying on its normal
   poll interval the whole time (`ANCHOR_POLL_INTERVAL_SECONDS`).

## Rebuilding the read model from chain

**When:** `rm_scores` or `rm_agent_events` is suspected corrupted or
inconsistent with on-chain state (e.g. after a manual database edit, a
restore from an older backup — see below — or a bug in an indexer handler
that's since been fixed). Both tables are a **pure, deterministic
function of on-chain events** (invariant I6 — see `db/models.py`'s
`ReadModelScore` docstring), never source-of-truth data, so the correct
fix is never a manual `UPDATE`/`INSERT` — it's wiping the affected
table(s) and the matching indexer cursor(s) and letting the indexer
replay from genesis. This is the exact technique
`tests/test_indexer.py::test_indexer_rebuilds_read_model_identically_from_genesis`
verifies for real, and the same one alembic migration `7c76a6e1b5ee` used
in production instead of a backfill when `rm_agent_events` gained a new
NOT NULL column.

1. Stop the indexer first — replaying while it's still polling live
   creates a race between the wipe and its next iteration:

   ```bash
   docker compose stop indexer
   ```

2. Truncate the affected read-model table(s) and delete the matching
   cursor row(s) — cursor names are exactly what `indexer/main.py::run_once`
   passes to `_poll_traced` per event stream:

   | Read model table  | Cursor(s) to delete                                                                                              |
   |--------------------|-------------------------------------------------------------------------------------------------------------|
   | `rm_scores`        | `TrustScoreRegistryV2`                                                                                         |
   | `rm_agent_events`  | `AgentIdentityRegistryV2:AgentRegistered`, `AgentIdentityRegistryV2:AgentRevoked`, `AgentIdentityRegistryV2:IntegrityViolation` |

   ```sql
   TRUNCATE TABLE rm_scores;
   DELETE FROM indexer_cursor WHERE contract_name = 'TrustScoreRegistryV2';

   -- and/or, for rm_agent_events:
   TRUNCATE TABLE rm_agent_events;
   DELETE FROM indexer_cursor WHERE contract_name IN (
     'AgentIdentityRegistryV2:AgentRegistered',
     'AgentIdentityRegistryV2:AgentRevoked',
     'AgentIdentityRegistryV2:IntegrityViolation'
   );
   ```

   Deleting a cursor row (rather than zeroing it) is deliberate —
   `cursor.py::resolve_start_block` treats a missing cursor as "start
   from `deploy_block`" (every `_poll_traced` call in `indexer/main.py`
   leaves this at its default of block 0, i.e. genesis, in this
   codebase), so there's no block number to look up or hardcode here.
3. Restart the indexer: `docker compose start indexer`. It will re-scan
   every block from genesis forward on its normal poll loop — for a
   chain with a large block range since deploy, this
   can take a while (`indexer_poll_lag_blocks` will show it catching up);
   for local dev against Anvil it's typically seconds.
4. Confirm the rebuild actually reproduced the data: spot-check a few
   known runs via `GET /trust-scores?run_id=...` and a few known agents
   via `GET /agents/{agent_id}/verify?code_hash=...` (using each agent's
   currently-registered hash) against what you'd expect from on-chain
   state (`cast call` the contract directly for the same agent/run)
   rather than just checking the table is non-empty.

## Restoring the database from a snapshot

**When:** a bad migration, an operational mistake (accidental delete/
update against `runs`, `steps`, `anchor_outbox`, etc.), or disaster
recovery. Unlike the read model (rebuildable from chain, see above),
`users`/`organizations`/`projects`/`api_keys`/`runs`/`steps`/
`anchor_outbox`/`anchor_batches` are genuine source-of-truth data with no
on-chain equivalent to replay from — a snapshot restore is the only
recovery path for those.

**Taking the snapshot** (do this on a schedule, before this procedure is
ever needed — `pg_dump` against a live database is safe to run
concurrently, it doesn't block writers):

```bash
docker compose exec -T postgres pg_dump -U trustchain -d trustchain --format=custom \
  > "trustchain-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

**Restoring:**

1. Stop everything that writes to or reads from the database — a restore
   against a live database races every in-flight write, and a partially-
   overwritten table is worse than the problem being fixed:

   ```bash
   docker compose stop api anchor-worker indexer
   ```

2. Restore into a **fresh** database rather than overwriting the live one
   in place, so a bad dump file or a restore that fails partway through
   never leaves you worse off than before you started:

   ```bash
   docker compose exec -T postgres createdb -U trustchain trustchain_restore
   docker compose exec -T postgres pg_restore -U trustchain -d trustchain_restore \
     --no-owner --clean --if-exists < trustchain-<timestamp>.dump
   ```

3. Sanity-check the restored data before cutting over — row counts on a
   few key tables, the most recent `runs.created_at`, and the most recent
   `anchor_outbox` row's `id`, compared against what you expect for the
   snapshot's timestamp.
4. Cut over: rename the live database aside and promote the restored one
   (or point `DATABASE_URL` at `trustchain_restore` and skip the rename —
   either works, renaming keeps the naming convention `DATABASE_URL`
   already assumes):

   ```bash
   docker compose exec -T postgres psql -U trustchain -d postgres -c \
     "ALTER DATABASE trustchain RENAME TO trustchain_pre_restore_$(date -u +%Y%m%dT%H%M%SZ);"
   docker compose exec -T postgres psql -U trustchain -d postgres -c \
     "ALTER DATABASE trustchain_restore RENAME TO trustchain;"
   ```

5. Run migrations forward if the snapshot predates a schema change since
   applied elsewhere: `alembic upgrade head` (same command this repo
   already uses for every other migration — see any of this session's
   `alembic/versions/*.py` files for precedent).
6. Restart the stack: `docker compose start api anchor-worker indexer`.
7. The restored database's `runs`/`steps`/`anchor_outbox` reflect
   whatever was true at snapshot time — anything written between the
   snapshot and the incident (or written on-chain but not yet reflected
   in the restored `anchor_batches`) is now behind chain reality. Follow
   "Rebuilding the read model from chain" above unconditionally after any
   restore — it's the only way to know `rm_scores`/`rm_agent_events`
   actually match what the restored write-model + real chain state say,
   rather than assuming the restore alone was sufficient.

**Drill log** (steps 1-3 actually run, against the real live dev stack —
not a hypothetical):

- **2026-08-16, against `docker-compose.yml`'s dev stack** (`postgres:16`,
  volume `trustchain_trustchain_postgres_data`), all 17 tables present
  (schema at migration `f1e2d3c4b5a6`).
- Ran the exact `pg_dump` command above against the live database while
  `api`/`anchor-worker`/`indexer` stayed up (no stop needed for the dump
  itself, confirming the "safe to run concurrently" claim) — produced a
  60528-byte custom-format dump in under a second.
- Restored into a fresh, isolated database (`trustchain_restore_drill_verify`,
  not `trustchain_restore` — named to make unmistakably clear it was a
  disposable verification target, never intended for the cutover steps
  below) via the exact `createdb`/`pg_restore` commands above — restore
  itself took under 1 second for this dev-sized dataset.
- **Verified, not assumed:** row counts for all 10 non-empty/tracked
  tables (`users`, `organizations`, `projects`, `runs`, `steps`,
  `anchor_outbox`, `anchor_batches`, `agents`, `rm_scores`,
  `rm_agent_events`) matched exactly between source and restored copy;
  spot-checked the `agents` table's actual row content (agent_id, model,
  version, code_hash) byte-for-byte identical; confirmed
  `alembic_version` matched (`f1e2d3c4b5a6`) between both databases,
  proving the dump captures schema state consistently with data.
- Cleaned up the verification database (`dropdb`) immediately after —
  it never served traffic and was never pointed at by any running
  service's `DATABASE_URL`.
- **Deliberately NOT run in this drill:** step 4's actual cutover
  (`ALTER DATABASE ... RENAME`) against the live serving database. That
  step is a genuinely destructive, hard-to-reverse action against
  whatever database is actively serving `api`/`anchor-worker`/`indexer`
  — rehearsing dump→restore→verify (steps 1-3, done for real above) is
  what proves the backup is valid and the restore procedure works;
  actually swapping it into production is an operational decision a
  real incident response makes deliberately, with the stack already
  stopped per step 1, not something to exercise casually against a live
  environment just to check a box. If this runbook is drilled again
  with a disposable throwaway Postgres instance (not the shared dev
  stack), running the cutover steps too would be worth doing.
- **What this confirms:** the documented commands are real and correct
  (no typos, no missing flags, no assumed-but-wrong tool availability —
  `pg_dump`/`pg_restore`/`createdb`/`dropdb` all work exactly as
  written via `docker compose exec`), and a dump taken from this stack
  genuinely round-trips through `pg_restore` with zero data loss or
  corruption for every table that matters.
