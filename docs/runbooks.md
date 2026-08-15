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
dashboard "TrustChain". Metrics: `docker/prometheus/prometheus.yml`'s
scrape targets, defined in `backend/observability.py`. Logs: structured
JSON via `structlog` (`backend/logging_config.py`) — every line carries
`request_id`, and pipeline-related lines also carry `run_id`; grep/filter
on those to follow one request or run through everything it caused.

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
