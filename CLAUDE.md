# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TrustChain is a multi-agent AI system (LangGraph pipeline: researcher →
validator → scorer → reporter) where every agent step is durably
recorded in Postgres and then Merkle-batch-anchored on-chain (Monad
testnet / local Anvil), so a run's audit trail is independently
verifiable without trusting TrustChain's own database. It's a monorepo:
FastAPI backend, Next.js frontend, Solidity contracts (two live
generations, V1 and V2), two SDKs + a CLI, and two MCP tool servers.

`docs/architecture.md` is the authoritative map of the system — read it
before making non-trivial changes. `docs/adr/` explains *why* specific
designs were chosen (transactional outbox, Merkle batching, RLS as
defense-in-depth, pluggable signer backends, etc.) — check there before
re-litigating a decision that looks odd at first glance; it's likely
deliberate and already reasoned through.

## Commands

### Local dev stack
```bash
./start.sh                          # plain-process local dev (backend + frontend + MCP servers, no Docker)
docker compose up --build           # the 9 services that make the product work: postgres, redis, anvil, api, anchor-worker, indexer, integrity-watchdog, MCP servers
docker compose --profile observability up --build   # same 9, plus alloy (10 total)
docker compose up -d --build api anchor-worker indexer   # rebuild just the backend services
```
Observability used to be a self-hosted prometheus/grafana/loki/promtail
quartet (~514 MiB, ~34% of the full stack's idle RAM); it's now a single
`alloy` service (`docker/alloy/config.alloy`, `grafana/alloy` image)
that scrapes the same 4 metrics targets and tails the same per-container
logs, then forwards both to Grafana Cloud instead of storing them
locally — visualization happens in Grafana Cloud's own hosted UI, not a
local Grafana. Measured at ~98 MiB (`docker stats`), a ~416 MiB (~81%)
drop versus the old quartet, confirmed via Alloy's own self-metrics
(`prometheus_remote_storage_samples_failed_total=0`,
`loki_write_dropped_bytes_total=0` at `:12345/metrics`, checked from
inside the container's network namespace since Alloy's UI binds to
127.0.0.1 only) after a real push to this repo's actual Grafana Cloud
stack — not just "the container started". Credentials
(`GRAFANA_CLOUD_PROMETHEUS_*`/`GRAFANA_CLOUD_LOKI_*` in `backend/.env`,
gitignored) are two separately-scoped Access Policy tokens
(`metrics:write` / `logs:write`) read by Alloy via `sys.env(...)` at its
own startup — `config.alloy` itself is committed and carries no
secrets. Still `profiles: ["observability"]`, still opt-in via
`--profile observability` — routine local dev doesn't need cloud
metrics/logs either. `docker/prometheus/`, `docker/loki/`,
`docker/promtail/`, `docker/grafana/` are unreferenced now but
deliberately left in place (same convention as the kept V1 contracts).
`docker/prometheus/alerts.yml`'s 19 rules (5 groups) ARE live, though —
`scripts/push_alert_rules.py` loads them into Grafana Cloud's Mimir
Ruler API verbatim (Mimir accepts the same rule-group YAML Prometheus
does), confirmed via a real `GET` back against the ruler API, not just
the POST status codes. Re-run that script by hand after editing
alerts.yml. Still outstanding: Grafana Cloud's Alertmanager has no
contact point/route configured yet, so these rules evaluate and show as
firing in the UI but won't actually notify anyone until that's set up
(Alerting → Contact points, in the Grafana Cloud UI — an account-side
click-through step, not something scriptable from here).
Anvil has no persistent volume — a full `--build` (even of an unrelated
service, due to BuildKit provenance attestations changing every image's
digest and cascading a recreate through the compose dependency graph)
resets it to block 0. After that, V2 contracts must be redeployed and
`backend/contracts/addresses_v2.json` rewritten before anything that
touches chain will work again:
```bash
cd contracts
PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  forge script script/DeployV2.s.sol --rpc-url http://localhost:8545 --broadcast
python3 backend/scripts/write_v2_addresses.py --chain-id 31337
docker compose exec -T postgres psql -U trustchain -d trustchain -c "TRUNCATE TABLE indexer_cursor;"
docker compose restart api anchor-worker indexer   # addresses_v2.json is bind-mounted, not baked into the image — restart is enough, no rebuild needed
```
The `TRUNCATE` step is not optional — `indexer_cursor` lives in Postgres,
which (unlike Anvil) DOES persist, so it keeps pointing at whatever block
height the previous Anvil generation last reached. Against a brand-new,
lower chain, `indexer/poll.py::poll_once` computes `start_block >
latest_block` forever and returns 0 every cycle — with **no log line
either way** (it only logs `indexer_polled` when it actually handled
something), so a stuck indexer looks identical to a healthy idle one:
process running, DB/RPC connections established, zero errors, just
silently never indexing anything new ever again. Found by a real
freshly-registered agent never appearing in `GET /agents` no matter how
long the wait, and confirmed by directly inspecting `indexer_cursor` vs
the actual chain tip. `./start.sh` does this automatically now (same
`NEEDS_DEPLOY` check that redeploys the contracts) — this manual drill
didn't, and needed the same fix.
That private key is Anvil's well-known default account #0 — the same
one `docker-compose.yml` forces for the `anchor-worker`/`api` services'
V2 connection and `backend/tests/conftest.py`'s `ANVIL_KEY` uses for
`chain_settings`-gated tests, so it's the one DeployV2.s.sol's
local-dev default (`RELAYER_ADDRESS` unset) actually grants `ANCHOR_ROLE`
to. Deploying with a different key breaks anything that expects that
role grant.

`backend/tests/conftest.py` self-provisions real ephemeral Postgres/Redis
via Testcontainers when `DATABASE_URL`/`REDIS_URL` aren't already set —
plain `pytest` from a clean checkout just works, no docker-compose stack
needed at all (only Docker itself, to run the containers). This is the
default and normally what you want.

If you instead explicitly set `DATABASE_URL`/`REDIS_URL` to point at the
docker-compose stack's own Postgres/Redis (e.g. to inspect state with
real accumulated data, or to exercise
`tests/test_chaos.py::test_chaos_postgres_outage_ready_degrades_and_recovers`,
which needs the stable, externally-managed instance and skips itself
against a self-provisioned one — see that file's own comment), stop
`anchor-worker`/`indexer`/**`integrity-watchdog`** first — `isolated_db`'s
autouse fixture truncates the tenant tables before and after every test,
and any of these three still polling that same database creates real
lock contention/deadlocks against the truncate, not just logical noise.
`integrity-watchdog` is easy to miss here (confirmed the hard way: a full
suite run with it still up got a real `DeadlockDetectedError` on
`TRUNCATE ... users, ...` in `tests/test_multi_tenancy.py`, gone the
moment `integrity-watchdog` was stopped and the same 11 tests re-run
clean) — it polls Postgres on its own cycle exactly like the other two,
it's just easy to forget since it isn't a docker-compose service under
`./start.sh`'s plain-process path (that script launches it as bullet
[6/7], a raw PID, not a container):
```bash
docker compose stop anchor-worker indexer   # if running via docker compose
# or, under ./start.sh's plain-process path, kill their PIDs directly —
# anchor-worker, indexer, AND integrity-watchdog
# ...DATABASE_URL=... REDIS_URL=... pytest...
docker compose start anchor-worker indexer   # or relaunch the killed processes
```

### Backend (`backend/`, Python 3.12+, venv at repo root)
```bash
pytest                                                        # full suite (self-provisions Postgres/Redis via Testcontainers if not already set; Anvil-gated tests skip cleanly without a local Anvil)
pytest tests/test_gas.py -v                                   # one file
pytest tests/test_gas.py::test_build_fee_params_... -v        # one test
pytest -m unit                                                # pure-logic tests only, no real DB/chain/network
pytest -m integration                                         # tests touching a real Postgres/Redis/Anvil/subprocess/HTTP dependency
ruff check .                                                   # lint (blocking in CI)
ruff format --check .                                          # format check (non-blocking — codebase predates it)
mypy --ignore-missing-imports --explicit-package-bases --exclude 'tests/' .   # (non-blocking)
bandit -r . -x ./tests,./.venv --severity-level medium         # security lint
alembic upgrade head                                           # apply migrations (real deployments; pytest builds schema straight from models instead)
alembic revision -m "description"                              # new migration — hand-write it (see recent versions/ files for the add_column/nullable-first-then-backfill convention on existing tables)
```
`tests/conftest.py`'s `pytest_collection_modifyitems` auto-classifies
`unit` vs `integration` by scanning each test's own source (and
transitively-reachable local helpers) for markers like `TestClient`,
`get_sessionmaker`, `requires_anvil` — a new test that touches real
infra through a new helper function needs that helper's name added to
`_INTEGRATION_SOURCE_MARKERS` or it'll be miscategorized.

### Frontend (`frontend/`, Next.js 14 App Router)
```bash
npm run dev            # dev server
npm run build           # production build
npx tsc --noEmit         # type-check
npm run lint             # eslint
```

### Contracts (`contracts/`, Foundry)
```bash
forge build --sizes
forge test -vvv
forge test --match-test testSomeName -vvv     # one test
forge test --match-contract SomeContractTest  # one contract's tests
forge fmt --check
forge snapshot --check                         # gas-regression gate against .gas-snapshot
forge coverage --report lcov --report summary --no-match-coverage "script/"
slither . --exclude-informational --exclude-low --exclude-optimization
```

### SDKs (`sdk/python/`, `sdk/python-cli/`, `sdk/typescript/`)
All three are real, independently-versioned publishable packages
(`trustchain-sdk` on PyPI and npm — same name, different registries —
plus `trustchain-cli` on PyPI), not yet actually published anywhere.
Their own test suites are deliberately integration tests against a live
backend, not mocks — see each package's README for what real bugs that
caught.
```bash
cd sdk/python  && pip install -e ".[dev]" && pytest tests/ -v
cd sdk/python-cli && pip install -e ".[dev]" && pytest tests/ -v
cd sdk/typescript && npm install && npm test
```

## Architecture

### Backend package layout (`backend/`)
- `main.py` — the FastAPI app: auth, run orchestration (`POST /run-agent`),
  read endpoints, and the third-party self-instrumentation surface
  (`POST /agents`, `POST /steps`) SDK users hit directly.
- `agents/` — the LangGraph pipeline itself (`pipeline.py` wires
  researcher → validator → scorer → reporter; `base.py` holds shared
  `AgentState`/`log_step`/LLM-token-tracking plumbing every node imports).
- `anchor_worker/` — separate process; claims pending steps from the
  outbox (`FOR UPDATE SKIP LOCKED`), batches them into a Merkle tree,
  submits `anchorBatch()`. Holds a Postgres advisory lock as sole nonce
  authority so two replicas can't race on the signing key's nonce.
- `indexer/` — separate process; polls on-chain events into the read
  model and reconciles any batch the anchor worker crashed before
  confirming.
- `db/` — models, migrations, the read model, and `tenancy.py`
  (org/project/API-key logic — every tenant-scoped table carries or
  resolves to a `project_id`).
- `blockchain/` — `client.py` (V1 bridge), `score_writer.py`/
  `identity_writer.py` (V2 writers), `gas.py` (EIP-1559 fee estimation +
  `eth_estimateGas`-based limits), `resilient_provider.py` (RPC
  fallback/circuit breaker), `signer.py` (pluggable local/KMS/Vault
  signing backends).
- `rate_limit.py` — Redis token-bucket + login-backoff, applied per
  project (write paths) and per IP (unauthenticated/pre-auth surface).

### Two live contract generations, by design
V1 (`contracts/src/*.sol`) is single-tenant, per-action anchoring, kept
**read-only** now (serves `/verify`/`/verify/tamper-demo`'s live demo on
whichever chain it was originally deployed to). V2
(`contracts/src/v2/*.sol`) is multi-tenant, Merkle-batched, with
role-separated `DEFAULT_ADMIN_ROLE` (rare, ideally a multisig) /
`ANCHOR_ROLE` (routine batch anchoring) / `REGISTRAR_ROLE` (routine
agent registration) — all new writes go here. See ADR-0003.

### The durability guarantee
`agents/base.py::log_step` writes a `steps` row and an `anchor_outbox`
row in the **same** Postgres transaction — a crash between "recorded
the step" and "queued it for anchoring" is unobservable from outside;
either both commit or neither does. Actual on-chain anchoring happens
later, out of band, batched. See ADR-0001/ADR-0002.

### Multi-tenancy (invariant I7)
No tenant can read or write another tenant's runs, agents, or scores —
enforced at two independent layers: application-level `WHERE project_id
= ...` filtering AND Postgres Row-Level Security under a separate,
non-superuser `trustchain_api` role, so a single missed filter in a
future endpoint doesn't silently become a cross-tenant leak (ADR-0006).
`docker-compose.yml`'s `api` service connects as `trustchain_api`
(RLS-bound); `anchor-worker`/`indexer` connect as the `trustchain`
superuser (RLS doesn't apply to them — they work across all tenants by
design). Tests intentionally bypass RLS for cross-tenant fixture setup
except `test_row_level_security.py`, which exercises RLS itself via its
own dedicated connection.

### API versioning
Every route is mounted twice — unprefixed (legacy) and under `/v1`
(canonical) — pointing at identical handlers, plus a small
`v1_only_router` for the few endpoints whose Appendix-A-documented `/v1`
name doesn't literally match the legacy one. See ADR-0005.

### Budgets and circuit breakers follow one shared pattern
Gas spend (`organizations.gas_budget_wei`/`gas_spent_wei`) and LLM token
spend (`organizations.token_budget`/`tokens_spent`) are both: a nullable
per-org ceiling (`NULL` = unlimited, the safe default for existing
orgs), a monotonically-incremented real-spend counter updated via an
atomic `UPDATE ... SET x = x + :delta` (never read-then-write), and a
`get_org_*_status()`/`record_*_spend()` pair in `db/tenancy.py`. A new
per-org budget should follow this exact shape rather than inventing a
new one.

### Observability
Prometheus metrics live in one shared module (`backend/observability.py`)
specifically so a metric name can't drift between what a process emits
and what `docker/prometheus/alerts.yml`/Grafana queries for. No metric
label carries a tenant identifier (unbounded cardinality risk) — per-
tenant drill-down goes through structured logs instead
(`logging_config.py`'s request/run/org-id correlation).

## Working conventions specific to this repo

- **Comments explain *why*, not *what*** — extensively, and the
  existing comments are load-bearing context (they cite the specific
  incident, constraint, or a `plan §N`/ADR that produced the current
  shape). Read the comment before changing the code it's attached to;
  match that density in new code only where the reasoning is similarly
  non-obvious.
- **Verify against real infrastructure, not mocks**, wherever practical
  — this repo's own test suites are built that way deliberately (see
  the SDK note above; several real bugs were only ever caught this way).
  When a task can't be verified for real (e.g. no real npm/PyPI account
  credentials to actually publish a package), say so explicitly rather
  than claiming it's confirmed.
- **Non-blocking CI gates are deliberate, not oversights** — `ruff
  format --check`, `mypy`, npm's `audit`, etc. are informational against
  a codebase that predates them; don't "fix" a job's blocking-ness
  without being asked.

## Session handoff notes (as of 2026-08-20)

Point-in-time status for picking this work back up in a new session —
prune/replace this section once it's stale rather than letting it
accumulate. **The `TaskList`/`TaskCreate`/`TaskUpdate` task tracker does
NOT reliably persist across sessions** (confirmed twice now, in two
different sessions) — don't treat it as a durable cross-session record;
this Markdown section is the actual durable one.

### Later same day (2026-08-20): Phase 5 pre-flight — Brevo enabled for
real, Grafana Cloud credentials actually wired in, three stale processes
found and killed

Picked up on the explicit ask "before I start with frontend, set up
Grafana Cloud and Brevo (or anything else that needs it)." Found real
gaps in both, despite the "Observability migrated to Grafana Cloud"
section directly below claiming this was already done — that section
describes real work (the alert rules ARE live in Mimir, the contact
point IS configured), but the six `GRAFANA_CLOUD_*` credentials
themselves were never actually persisted into `backend/.env`, so
`alloy` had nothing to authenticate with. Root cause is unconfirmed
(likely: an earlier session generated/used tokens inline for one-off
verification calls without writing them to the file) — worth being
aware this specific gap can recur if credentials are minted again
without also being saved.

- **Brevo**: `BREVO_API_KEY`/`EMAIL_FROM`/`BREVO_API_URL` were already
  present in `.env`, but `EMAIL_BACKEND` was still `console` (no real
  email was sending) AND `EMAIL_FROM` (`shreshthashreshtha28@gmail.com`)
  was not a verified Brevo sender — only `kalsotranipun@gmail.com` was
  (confirmed on Brevo's own Senders page). Fixed both: `EMAIL_BACKEND=
  brevo`, `EMAIL_FROM=kalsotranipun@gmail.com` (the `email_from_name`
  default of "TrustChain" already combines correctly, matching the
  verified sender's display name). Verified for real, not just
  configured: a fresh signup against a locally-restarted backend logged
  `POST https://api.brevo.com/v3/smtp/email "HTTP/1.1 201 Created"`.
- **Grafana Cloud**: created two least-privilege access policies in the
  `luckybus1510` org matching what `.env.example` already documented as
  the intended design (separately-scoped, not one shared token) —
  `trustchain-prometheus-write` (`metrics:write` only) and
  `trustchain-loki-write` (`logs:write` only) — generated one token
  each, and wrote all 6 `GRAFANA_CLOUD_*` values into `backend/.env`.
  Grafana Cloud's own UI made this harder than it should have been: the
  "Create token" reveal screen never actually rendered in a way
  Chrome-automation tooling could read (several attempts silently
  succeeded server-side with no visible confirmation — cleaned up 3
  stray unretrievable tokens afterward), so the actual secret values
  were captured by patching `window.fetch` in the page and reading the
  real API response body directly, not by reading the UI. Verified for
  real: brought up `docker compose --profile observability`, and
  `alloy`'s own self-metrics (`:12345/metrics`, read via a throwaway
  `curlimages/curl` container sharing alloy's network namespace, same
  method the prior Grafana Cloud session used) showed
  `prometheus_remote_storage_samples_total=100` /
  `samples_failed_total=0` and `loki_write_sent_bytes_total=71342` /
  `loki_write_dropped_bytes_total=0` — real samples/logs actually
  landing in Grafana Cloud, not just "the container started."
- **Found and killed 3 stale leftover processes from the *previous*
  session's `./start.sh` run** (`indexer.main`, `anchor_worker.main`,
  `integrity_watchdog.main` — all started within the same second on
  2026-08-19, none ever cleaned up): they were squatting on ports
  9101/9102, which is exactly why the real Docker `anchor-worker`/
  `indexer` containers couldn't bind and start after the Anvil reset
  below. This is the "Recurring pattern" section's stray-process
  problem happening again, concretely — worth a `ps aux | grep -E
  "python3 -m (indexer|anchor_worker|integrity_watchdog)"` sanity check
  at the start of any session before assuming a container port-bind
  failure is a real bug.
- **Anvil reset again**: verifying the observability profile required
  `--build`, which (per this file's own documented trap, immediately
  below) recreated Anvil and reset it to block 0. Ran the full documented
  redeploy drill (`DeployV2.s.sol` broadcast, `write_v2_addresses.py`,
  `TRUNCATE indexer_cursor`) — confirmed working since it's the same
  drill this file already describes.
- **Still broken, NOT fixed this session (real, narrow finding)**: the
  Docker `anchor-worker` container crash-loops on a cold start even
  after the stray-process/port conflict above was cleared —
  `anchor_worker/nonce_lock.py`'s Postgres advisory-lock acquisition
  (the very first DB touch at process startup) has no retry/backoff
  around the *connection* itself, only around "lock not yet held" — so
  a transient DNS resolution blip during container startup (confirmed
  the network/DNS itself was fine independently — `getent hosts
  postgres` and a direct `eth_blockNumber` call to `anvil` both
  succeeded from a throwaway container on the same network) crashes the
  whole process instead of retrying. `indexer` doesn't hit this same
  race. Out of scope for this session (would mean editing
  `nonce_lock.py`, not something asked for) — flagging for whoever picks
  up backend robustness work next.
- **Frontend pre-flight checked and clean**: `frontend/lib/api.ts`'s
  `NEXT_PUBLIC_API_URL` already defaults to `http://localhost:8000`
  (no `.env.local` needed for local dev), and `main.py`'s CORS
  allowlist already covers `localhost:3000`/`3001` — nothing else
  needed there before Phase 5 starts.

### Earlier same day (2026-08-20): closed Phase 4's 3 known caveats, found and
fixed a major previously-unknown bug, migrated observability to Grafana
Cloud

Picked up after Phase 4 was merged (PR #30) to close out its own
handoff caveats before Phase 5 (frontend) starts:

- **TypeScript SDK's real-backend test suite**: 31/31 passing (Phase
  4's handoff had explicitly flagged this as not run yet).
- **`trustchain-cli`'s test suite**: 21/21 passing — required fixing a
  real bug first (`_fresh_user_credentials()`/`_fresh_api_key()` never
  got the Phase 4 email-verification-gate fix the other two SDKs'
  fixtures received, so 13/16 tests were failing on `403
  email_not_verified` before the fix), then adding coverage that was
  genuinely missing (`verify-content` and `verify-run` command tests).
- **`scripts/e2e_demo.py`'s docstring-promised `/dev/null` stdin
  fallback**: actually implemented (it didn't exist before — the
  docstring was aspirational) and verified with a full unattended run:
  **"ALL STAGES PASSED"**, all 8 stages, fresh signup through tenant
  isolation, against a real torn-down-and-rebuilt stack.
- **A second major, previously-unknown bug, found only by actually
  running things**: `indexer_cursor` lives in Postgres, which (unlike
  Anvil) persists across `--build`/reset cycles. After any Anvil reset,
  the cursor keeps pointing at the old chain's block height;
  `indexer/poll.py::poll_once`'s `start_block > latest_block` early
  return means the indexer silently never indexes anything again —
  **no log line either way**, indistinguishable from a healthy idle
  indexer by any log or metric. Found via a real confirmed on-chain
  `AgentRegistered` tx that never appeared in `GET /agents`. Fixed in
  both `start.sh` (automatic) and the manual redeploy drill documented
  above.
- **Observability migrated from a self-hosted quartet to Grafana
  Cloud**: see the "Local dev stack" section above — `alloy` service,
  ~98 MiB measured vs. the old ~514 MiB, and `scripts/push_alert_rules.py`
  loaded all 19 alert rules into Grafana Cloud's Ruler API. Both
  verified against the real Cloud stack (self-metrics showing 0 failed
  samples/dropped bytes; a real `GET` confirming all 5 rule groups
  present) — not just "it started."
- **Alertmanager routing — closed.** A `TrustChain-email` contact point
  (email → `kalsotranipun@gmail.com`) and a default notification policy
  routing to it already existed in the Cloud UI by the time this was
  checked (verified via Claude in Chrome driving the actual Grafana UI —
  `/alerting/notifications` showed the contact point, `/alerting/routes`
  showed the Default policy's "Delivered to TrustChain-email"). Ran the
  contact point's own **Test** button for real confirmation beyond just
  reading the config: Grafana returned "Test notification sent
  successfully". The contact point's "delivery attempts" counter still
  read 0 afterward — that counter only tracks real alert-triggered
  sends, not manual tests, a UI distinction rather than a failure
  signal. This closes every gap flagged since Phase 4 — backend is done,
  clear to start Phase 5 (frontend).

### Phase 4 — complete, merged via PR #30

Every step of the Phase 4 plan (email verification, password reset,
SDK/CLI `verify_content()` + typed alert forensics, a real demo agent,
`scripts/e2e_demo.py`, G6/G7/G8, RLS coverage for `steps_history`, three
new ADRs, `docs/phase5-frontend-contract.md`) is implemented and
verified against real infrastructure — see `docs/e2e-walkthrough.md`,
which documents the actual verified behavior (including several real
inaccuracies found in the original plan text along the way — wrong SDK
scope name, `/trust-scores` needing `run_id`, etc. — corrected there,
not just worked around).

**Two genuine, previously-unknown bugs were found and fixed this
session**, both only findable by actually running the full tamper →
deletion → alert flow end-to-end, not by reading code:

1. **A step deleted after being anchored never raised any alert for any
   org, at all.** `integrity_watchdog/tenancy.py::group_steps_by_org`
   resolved org via a join through the `steps` table — exactly the row
   that was just deleted. Fixed with a `steps_history.project_id`
   fallback (that table is denormalized specifically to survive this).
   `sweep_merkle_roots`'s "missing" branch also never called the
   forensic-attribution lookup the edit-detection path already had —
   fixed alongside it. This means the single most damaging tamper case
   (erasing the evidence outright, not just editing it) was silently
   unattributable in the shipped Phase 3 code, for every deployment,
   until this session.
2. `_forensic_evidence`'s "most recent steps_history row" query could
   pick the wrong row on a same-second timestamp tie — fixed with an
   `id` tiebreaker.

Both have real regression tests
(`backend/tests/test_integrity_detectors.py::
test_deleting_the_only_step_in_a_batch_still_raises_an_alert` is the one
that actually exercises bug #1 — a sibling test with a 3-step batch does
NOT, since surviving steps in the same batch mask the bug; see that
test's own docstring for why).

**Real verification performed, not just claimed:**
- `scripts/e2e_demo.py` passed all 8 stages end-to-end against a
  genuinely torn-down-and-rebuilt stack (`docker compose down` +
  `./start.sh` from scratch) — the strongest single piece of evidence,
  since it exercises signup → verify → invite → roles → real SDK agent →
  stats → tamper → forensic email/verify-content → deletion → tenant
  isolation as one real sequence.
- `forge test`: 151/151 passing (contracts untouched this phase, as
  expected).
- Backend pytest: every individual test file touched this phase passed
  cleanly in isolation after all changes settled (test_email_verification.py,
  test_password_reset.py, test_integrity_detectors.py,
  test_row_level_security.py, test_main.py, test_permissions.py). A
  final whole-suite run was in progress (57%+ complete, zero failures
  observed) when the user closed it for taking too long — not completed
  to 100% in one single run, but every piece of it that DID run, plus
  every file individually, was clean.
- Python SDK (`sdk/python/tests/`): 24/25 passed against the live
  Phase-4-patched backend (1 legitimate skip — a run genuinely couldn't
  complete without a real `PRIVATE_KEY`; 1 transient failure on a shared
  run confirmed to pass cleanly in isolation, see below).
- TypeScript SDK: `npm run build`/`tsc --noEmit` clean for every file
  this phase touched (one pre-existing, unrelated `cli.ts` type error
  confirmed via `git stash` to predate this phase — not fixed, out of
  scope). Its own real-backend test suite (`sdk/typescript/tests/`) was
  **not** run this session — worth doing in a fresh session before
  calling Phase 4 fully closed on that specific package.
- `trustchain-cli`'s own test suite was **not** run this session either
  — same caveat.

### Environment constraints (this sandbox specifically)
- **Ports 8000 and 8001 are occupied by unrelated, pre-existing
  root-owned services on this shared sandbox** (`api.main:app` on 8000,
  something else on 8001) — not anything this repo runs. This meant
  every real verification this session ran the API on port **8010**
  instead of the repo's real default (8000), with `V2_RPC_URL`/
  `V2_PRIVATE_KEY` set inline the same way `start.sh` sets them for the
  real port-8000 invocation. **The SDK test suites' `BASE_URL` constants
  were reverted back to the correct `http://localhost:8000` before this
  session ended** — do not assume they're still pointed at 8010; check
  `git diff` if anything looks off. A fresh session on a clean sandbox
  (or one where nothing else claims 8000/8001) should not need this
  workaround at all.
- **A fresh Python process's first HTTP connection to a busy local
  server can genuinely exceed a 2-second timeout** in this sandbox under
  load (many concurrent processes: Postgres/Redis/Anvil containers, API,
  anchor-worker, indexer, watchdog, plus whatever else is running) —
  confirmed via direct repeated `httpx.get(..., timeout=2.0)` calls
  failing consistently while a concurrent `curl` to the identical URL
  returned instantly. This is why `sdk/python/tests/test_client.py`'s
  own `_stack_is_up()` skip-check (hardcoded `timeout=2.0`, not changed
  by this session) can flake to "stack not reachable" even when it
  genuinely is — retry if a run comes back suspiciously all-skipped.
- The anchor-worker batches steps rather than anchoring instantly
  (ADR-0002) — `GET /steps/{id}/proof` 404s for a real handful of
  seconds after `log_and_wait` returns even under `start.sh`'s own
  5-second `ANCHOR_MAX_BATCH_AGE_SECONDS` override. Poll, don't assume
  immediate availability — `scripts/e2e_demo.py` and the SDK's own
  `test_get_proof_after_real_anchoring_verifies_locally_and_onchain`
  both do.
- Only Python 3.14 is installed locally (`/usr/bin/python3.14`, the
  repo's own `.venv`) — CI's matrix is 3.11/3.12. A "local repro passes"
  result on 3.14 is not proof CI will pass on either matrix version.
- `docker`, `anvil`/`forge` (Foundry), and Testcontainers-via-Docker all
  work directly in this sandbox.

### Recurring pattern worth knowing about
In an earlier session on this same repo, local file edits (made via the
Edit tool but explicitly not committed) showed up committed and pushed
under the user's own account without an explicit "commit this"
instruction ever being given in-session. Most likely explanation: the
user commits/pushes independently from another terminal or IDE while a
session is running. Worth a plain `git status`/`git log` sanity check at
the start of a new session rather than assuming file state matches
whatever this file or a task tracker entry implies — and worth asking
the user directly if authorship of a surprising commit is ever unclear,
rather than assuming it either way. Separately, this session confirmed
the user may also close/kill long-running background verification
commands themselves mid-run when they're taking too long — check
`ps`/`docker ps` for what's actually still alive rather than assuming a
command this assistant started is still running just because nothing
reported it as killed.
