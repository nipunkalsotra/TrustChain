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

## Session handoff notes (as of 2026-08-16)

Point-in-time status for picking this work back up in a new session —
prune/replace this section once it's stale rather than letting it
accumulate. The task tracker (`TaskList`, tasks in the #88+ range) is
the durable record of what's done/pending across sessions; this section
covers what the tracker doesn't: git state and environment gotchas
learned the hard way this session.

### Git / CI state
- Branch `phase2`, PR #24 open against `main` (`nipunkalsotra/TrustChain`,
  public repo).
- Two real backend-CI failures were diagnosed and fixed this session,
  landed in commits `5ac8959` and `29f318d` (both already on
  `origin/phase2`):
  1. `backend/tests/test_deprecation.py`'s two live-response tests hit
     `/health`, which needs a real `PRIVATE_KEY`/`MONAD_RPC_URL` (the V1
     bridge) — CI deliberately never configures those (see
     `main.py`'s `get_bridge_or_503` docstring), so `/health` 503s
     there. Local dev's own `backend/.env` has a real `PRIVATE_KEY`,
     which silently masked this — a real trap when comparing local vs.
     CI behavior. Fixed by pointing those tests at `/ready` instead
     (DB-only, always 200 regardless of chain/bridge state).
  2. `gitleaks` scans full git history (`fetch-depth: 0`); a since-fixed
     fake secret (`sk_live_abcdef123456`, introduced in `a5236c2`, fixed
     the very next commit) is still reachable at the commit that
     introduced it — a later fix can't erase it from history. Allowlisted
     in `.gitleaks.toml` (same pattern as the two other known-fake test
     keys already there) rather than rewriting pushed history.
  - Neither fix has been re-confirmed against a fresh real GitHub Actions
    run yet (this environment has no `gh` CLI / no push-capable GitHub
    credentials — see below) — only verified via faithful local repro
    (real Anvil + V2 deploy + Testcontainers-self-provisioned
    Postgres/Redis, `PRIVATE_KEY` unset to match CI exactly) and a real
    `gitleaks detect --log-opts="--all"` scan (0 leaks). Worth checking
    the actual PR #24 checks once a new session starts.
- **Uncommitted, local-only as of this writing**: CI build-caching added
  to all four workflow files (`test.yml`, `k6.yml`, `deploy.yml` —
  `actions/cache` for `contracts/cache`+`contracts/out` keyed on Solidity
  source hashes; `release.yml` — `docker/setup-buildx-action` +
  `cache-from`/`cache-to: type=gha` on the image build). Verified locally
  (`forge build` 5.6s → 0.18s on a cache hit; `actionlint`/PyYAML clean
  on all four files) but not committed — this session has no push access,
  so these are sitting in the working tree for the user to review/commit.
  Run `git status` first thing in a new session to check whether they're
  still there, already committed, or something else changed.

### Environment constraints (this sandbox specifically)
- No `gh` CLI, no authenticated GitHub push/log-access credentials.
  Diagnosing real CI failures here means: (a) the public REST API works
  unauthenticated for a public repo's runs/check-runs/**annotations**
  (`GET /repos/{owner}/{repo}/check-runs/{id}/annotations`), but (b) the
  logs endpoint (`GET .../actions/jobs/{id}/logs`) 403s without an
  authenticated token, and (c) GitHub's own web UI now requires sign-in
  to view raw job logs even on public repos (confirmed via WebFetch — it
  shows only the summary/annotations to anonymous viewers). When
  annotations alone aren't enough, a temporary CI step that captures the
  failure tail and emits it via `::error::` (readable through the public
  annotations API) is a working, non-destructive way to get real signal
  back — see the git history around commit `5ac8959`/`29f318d` for the
  pattern actually used. The other reliable path: ask the user to paste
  the failure directly from the GitHub UI, since they're logged in.
- Only Python 3.14 is installed locally (`/usr/bin/python3.14`, the
  repo's own `.venv`) — CI's matrix is 3.11/3.12, and there's no way to
  install those interpreters in this sandbox. A "local repro passes"
  result on 3.14 is not proof CI will pass; treat Python-version-specific
  behavior as an open question rather than ruled out.
- `docker`, `anvil`/`forge` (Foundry), and Testcontainers-via-Docker all
  work directly in this sandbox — a faithful CI reproduction (real Anvil,
  real V2 contract deploy, self-provisioned Postgres/Redis) is possible
  and was done this session; see git history for the exact command
  sequence if repeating it.

### Recurring pattern worth knowing about
Twice this session, local file edits (that this assistant had made via
the Edit tool but explicitly not committed) showed up committed and
already pushed to `origin/phase2` under the user's own account, without
an explicit "commit this" instruction ever being given in-session
(`5ac8959` and `29f318d`). Both times the content matched exactly what
was sitting in the working tree. Most likely explanation: the user
commits/pushes independently from another terminal or IDE while a
session is running. Worth a plain `git status`/`git log` sanity check at
the start of a new session rather than assuming file state matches
whatever this file or a task tracker entry implies — and worth asking
the user directly if authorship of a surprising commit is ever unclear,
rather than assuming it either way.
