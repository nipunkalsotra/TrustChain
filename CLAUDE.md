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
docker compose up --build           # full stack: postgres, redis, anvil, api, anchor-worker, indexer, MCP servers, prometheus, grafana
docker compose up -d --build api anchor-worker indexer   # rebuild just the backend services
```
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
docker compose restart api anchor-worker indexer   # addresses_v2.json is bind-mounted, not baked into the image — restart is enough, no rebuild needed
```
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
`anchor-worker`/`indexer` first — `isolated_db`'s autouse fixture
truncates the tenant tables before and after every test, and a live
anchor-worker still polling that same database creates real lock
contention/deadlocks against the truncate, not just logical noise:
```bash
docker compose stop anchor-worker indexer
# ...DATABASE_URL=... REDIS_URL=... pytest...
docker compose start anchor-worker indexer
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

## Session handoff notes (as of 2026-08-19)

Point-in-time status for picking this work back up in a new session —
prune/replace this section once it's stale rather than letting it
accumulate. **The `TaskList`/`TaskCreate`/`TaskUpdate` task tracker does
NOT reliably persist across sessions** (confirmed twice now, in two
different sessions) — don't treat it as a durable cross-session record;
this Markdown section is the actual durable one.

### Phase 4 — complete, on branch `phase4`

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
