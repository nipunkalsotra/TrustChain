import asyncio
import os
import re
from pathlib import Path

# Must run before any other import in this file (or any test module) has a
# chance to `import main` -> `import auth` -> `config.get_settings()`.
# jwt_secret has no default (see config.py) — it fails startup if unset, so
# it has to land in the environment before that first call, not inside a
# fixture function (fixtures run after collection-time imports already
# happened). Same reasoning for DATABASE_USE_NULL_POOL — db/engine.py's
# get_engine() is an lru_cache singleton, so whichever pool class is in
# effect on first call sticks for the rest of the process; it must be set
# before anything (including a fixture) touches the engine.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use-only")
os.environ.setdefault("DATABASE_USE_NULL_POOL", "true")
# Off by default across the suite: dozens of existing tests reuse a small
# handful of fixed passwords (seed_user_and_token's "testpassword123",
# test_auth.py's "hunter22", ...) purely as functional test fixtures, and
# — unsurprisingly for such well-known strings — every one of them IS a
# real HIBP breach hit (verified directly against the real API while
# building this check). Leaving the check on by default here would make
# nearly every signup-based test in the suite fail for a reason that has
# nothing to do with what that test actually verifies. The feature itself
# (auth_pwned.py, wired into POST /auth/signup) is still exercised for
# real, with the check deliberately re-enabled per test, in
# tests/test_auth_pwned.py.
#
# Must ALSO run before the Testcontainers block below, not just before any
# test/fixture: that block's `alembic.command.upgrade()` call transitively
# imports alembic/env.py, which calls config.get_settings() to read
# sqlalchemy.url — the FIRST such call anywhere in the process constructs
# and lru_cache's the Settings singleton for good. Found for real: with
# this setdefault positioned AFTER that block (its original position,
# before Testcontainers existed, when nothing this early ever touched
# get_settings()), check_pwned_passwords was permanently stuck at its
# field default (True) all session, breaking every signup-based test that
# reuses one of this suite's well-known passwords — not a flaky ordering
# issue, a deterministic one, since alembic upgrade always runs before
# this line did in the old order.
os.environ.setdefault("CHECK_PWNED_PASSWORDS", "false")


# ── Self-provisioned Postgres/Redis (Testcontainers) ────────────────────
#
# Before this existed, DATABASE_URL/REDIS_URL fell back to
# localhost:5432/6379 unconditionally — the suite assumed SOMETHING was
# already listening there (the docker-compose stack, or CI's `services:`
# containers), with no way to just run `pytest` from a clean checkout and
# have it work. It also meant local pytest runs shared the SAME Postgres
# the docker-compose anchor-worker/indexer were actively polling, which is
# exactly why CLAUDE.md has to tell you to `docker compose stop
# anchor-worker indexer` before running the suite locally — a live poller
# holding locks against the tables `isolated_db` truncates every test.
#
# Now: DATABASE_URL/REDIS_URL, if ALREADY set (CI's `backend` job sets
# both explicitly; a developer can still point at their own running
# instance for debugging with real accumulated data), are respected
# as-is — same fallback semantics the old `os.environ.setdefault` had,
# just with a better default. If NOT set, real ephemeral Postgres/Redis
# containers are started here (module scope — this runs once, at
# collection time, before any fixture or test-module import can reach
# get_settings()) and torn down in pytest_sessionfinish below. Verified
# for real: a full local run with docker-compose entirely stopped and
# these two env vars unset — including tests/test_row_level_security.py,
# which needs a real `alembic upgrade head` to have run for its
# `trustchain_api` role to exist, hence the explicit migration step below
# rather than relying on `_schema` fixture's plain `create_all_tables()`.
_owned_containers = []
# Read by tests/test_chaos.py, as an env var rather than a plain module
# attribute: pytest's own conftest auto-discovery can end up importing this
# file under two different sys.modules keys (bare "conftest" vs.
# "tests.conftest", if a test module explicitly does `from tests import
# conftest`) — two independently-executed copies of everything below,
# each with its own module-level state. Confirmed for real: a plain
# `POSTGRES_SELF_PROVISIONED = "DATABASE_URL" not in os.environ` module
# attribute came out wrong (False) when read from the "tests.conftest"
# copy, because BY THE TIME that copy's module body ran, the "conftest"
# copy had already set DATABASE_URL — so the same self-provisioning run
# looked, from that vantage point, like DATABASE_URL had been externally
# provided all along. os.environ itself has no such duplication problem
# (it's one process-global namespace no matter how many module copies
# exist), so that's what test_chaos.py checks instead.
#
# What it's for: Testcontainers-managed containers are designed for
# exclusive lifecycle management BY the Python process that started them
# (Ryuk reaps them on exit) — they're not meant to survive an out-of-band
# `docker stop`/`docker start` from outside that. Confirmed for real:
# doing exactly that made the postgres-outage chaos test correctly find
# and stop the right container (see test_chaos.py's own updated
# _postgres_container_name), but recovery then failed — the container
# never became reachable again within 240s of `docker start`, unlike a
# real docker-compose-managed instance restarting cleanly. That chaos
# scenario needs the STABLE, externally-managed Postgres to mean
# anything; it skips itself when this env var is set rather than failing
# on a false alarm.
_postgres_self_provisioned = "DATABASE_URL" not in os.environ

if _postgres_self_provisioned:
    os.environ["_TC_POSTGRES_SELF_PROVISIONED"] = "true"
    from testcontainers.community.postgres import PostgresContainer

    # username/password/dbname match docker-compose.yml's postgres service
    # (POSTGRES_USER/PASSWORD/DB=trustchain) because the RLS migration
    # (alembic/versions/9f3a1c7d5e2b_*.py) hardcodes `GRANT CONNECT ON
    # DATABASE trustchain TO trustchain_api` — a literal database name, not
    # parameterized off whatever the connecting engine's own URL says —
    # found by running this against Testcontainers' actual default
    # ("test") first and hitting a real `InvalidCatalogNameError`.
    _pg = PostgresContainer("postgres:16", username="trustchain", password="trustchain", dbname="trustchain", driver="asyncpg")
    _pg.start()
    _owned_containers.append(_pg)
    os.environ["DATABASE_URL"] = _pg.get_connection_url()

if "REDIS_URL" not in os.environ:
    from testcontainers.community.redis import RedisContainer

    _redis = RedisContainer("redis:7-alpine")
    _redis.start()
    _owned_containers.append(_redis)
    os.environ["REDIS_URL"] = f"redis://{_redis.get_container_host_ip()}:{_redis.get_exposed_port(6379)}/0"

# Both DATABASE_URL and REDIS_URL are now correctly set in os.environ (real
# or self-provisioned) — ONLY NOW is it safe to run anything that could
# construct the Settings singleton (get_settings() is @lru_cache'd, module-
# level, process-wide: whichever env state exists at its FIRST call sticks
# for the rest of the session, same reasoning as this file's
# CHECK_PWNED_PASSWORDS ordering comment above). alembic/env.py calls
# get_settings() to read sqlalchemy.url — running it any earlier (previously
# interleaved between the Postgres and Redis blocks above) meant Settings
# got constructed and cached BEFORE REDIS_URL was set, permanently locking
# in redis_url's field default (redis://localhost:6379/0) for the entire
# session regardless of what REDIS_URL was set to afterward. Undetected
# locally purely by coincidence — a docker-compose Redis happened to
# already be listening at that exact stale address — but broke every
# single test in CI, where nothing does. Real, found regression, not
# hypothetical: confirmed via a real GitHub Actions run.
if _postgres_self_provisioned:
    # `_schema` fixture below only runs create_all_tables() (a plain ORM
    # schema build, no roles/RLS) — real deployments and CI's `services:`
    # path both apply the actual Alembic migrations instead, which is what
    # creates the `trustchain_api` role + RLS policies
    # test_row_level_security.py needs. Running that here too keeps the
    # self-provisioned path equivalent to CI's real path, not a lesser one
    # that silently skips RLS coverage.
    from alembic import command
    from alembic.config import Config

    _alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    command.upgrade(_alembic_cfg, "head")


def pytest_sessionfinish(session, exitstatus):
    for container in _owned_containers:
        container.stop()


import pytest
from web3 import Web3

from db.engine import create_all_tables, truncate_all_tables

# ── Local Anvil chain fixtures, shared by test_anchor_worker.py and
#    test_indexer.py — both exercise real on-chain behavior against
#    docker-compose's `anvil` service rather than a mock, since the whole
#    point of the outbox/anchor-worker/indexer design is durability and
#    cross-language Merkle compatibility, neither of which a mock would
#    actually verify.
ANVIL_RPC = "http://localhost:8545"
# Anvil's well-known default account #0 — public, funded only on local
# chains anvil itself spins up, never anywhere with real value.
ANVIL_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _anvil_and_v2_available() -> bool:
    try:
        w3 = Web3(Web3.HTTPProvider(ANVIL_RPC, request_kwargs={"timeout": 2}))
        if not w3.is_connected() or w3.eth.chain_id != 31337:
            return False
    except Exception:
        return False
    from pathlib import Path
    return (Path(__file__).parent.parent / "contracts" / "addresses_v2.json").exists()


requires_anvil = pytest.mark.skipif(
    not _anvil_and_v2_available(),
    reason="local Anvil with deployed V2 contracts not reachable at localhost:8545",
)


@pytest.fixture
def chain_settings(monkeypatch):
    """Points anchor_worker.chain's AND indexer.chain's cached
    Web3/contract/signer singletons at local Anvil instead of
    get_settings()'s testnet default, and clears their lru_caches so each
    test gets a fresh instance built against that override."""
    from anchor_worker import chain as anchor_chain_module
    from config import Settings
    from indexer import chain as indexer_chain_module

    test_settings = Settings(
        jwt_secret="test-secret-not-for-production-use-only",
        database_use_null_pool=True,
        monad_rpc_url=ANVIL_RPC,
        private_key=ANVIL_KEY,
        anchor_max_batch_size=256,
        anchor_claim_timeout_seconds=5,
        anchor_max_attempts=3,
        # Near-zero, not zero (Field(gt=0)) — FLOOR()s to a 0s delay in
        # handle_submit_failure's SQL, so existing tests that requeue a
        # failed batch and immediately expect it reclaimable keep working
        # unchanged. test_exponential_backoff_delays_retry (below) uses
        # its own real, larger value to verify backoff actually delays.
        anchor_backoff_base_seconds=0.001,
        anchor_backoff_max_seconds=0.001,
        # 0 (ge=0, a deliberately valid value — see config.py's own
        # comment on why NOT a tiny positive number) — `now - oldest >=
        # 0` is always true since age can never be negative, so existing
        # tests that seed a couple of steps and expect them anchored on
        # the very next run_once() call keep working unchanged.
        # value to verify accumulation + age-triggered flush actually
        # happens (test_max_batch_age_flush_trigger, below), not just
        # that "flush every cycle" (this default) still works.
        anchor_max_batch_age_seconds=0,
        indexer_poll_interval_seconds=0.5,
    )
    monkeypatch.setattr("anchor_worker.chain.get_settings", lambda: test_settings)
    monkeypatch.setattr("indexer.chain.get_settings", lambda: test_settings)
    modules = (anchor_chain_module, indexer_chain_module)
    caches = [
        "get_w3", "get_signer", "get_audit_log_contract", "get_trust_score_contract",
        "get_identity_registry_contract",
    ]

    def _clear():
        for module in modules:
            for cache_name in caches:
                fn = getattr(module, cache_name, None)
                if fn is not None:
                    fn.cache_clear()

    _clear()
    yield test_settings
    _clear()


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create tables once per test session (idempotent — create_all no-ops
    on tables that already exist). Real deployments use the Alembic
    migrations in alembic/ instead; tests build straight from the models to
    stay decoupled from migration history."""
    asyncio.run(create_all_tables())


@pytest.fixture(autouse=True)
def isolated_db():
    """Every test starts and ends with empty tables — TRUNCATE, not a
    per-test database. See config.database_use_null_pool for why this is
    safe to call via asyncio.run() (a fresh event loop per call) even
    though other calls in the same test may go through TestClient's own
    persistent loop.

    Also clears redis_client.get_redis()'s lru_cache: that client's
    underlying connection is bound to whichever event loop first used it,
    and the `client` fixture spins up (and tears down) a fresh loop per
    test via TestClient — reusing a cached connection across that
    boundary raises "Event loop is closed" the moment a *later* test's
    Redis call needs to service a response queued under the *earlier*
    test's now-dead loop.

    And FLUSHes Redis's actual data, not just the client cache — rate
    limit buckets and login-backoff counters (rate_limit.py) are keyed
    by project_id/email/IP, and TestClient requests all share the same
    "client IP" from the app's point of view, so without a flush a login-
    failure test would leak backoff state into whichever test runs next
    and 429 something that should have been a clean 401. The cache-clear
    and the flushdb() that uses the freshly-cleared client have to happen
    inside the SAME asyncio.run() call — splitting them would reintroduce
    the "Event loop is closed" bug for the flush itself — and the cache
    is cleared AGAIN right after, so that throwaway loop's now-dead
    client isn't left cached for the actual test body to trip over."""
    import redis_client

    async def _flush_redis():
        redis_client.get_redis.cache_clear()
        await redis_client.get_redis().flushdb()

    def _reset_redis():
        asyncio.run(_flush_redis())
        redis_client.get_redis.cache_clear()

    _reset_redis()
    asyncio.run(truncate_all_tables())
    yield
    _reset_redis()
    asyncio.run(truncate_all_tables())


def seed_project(name: str = "test project") -> int:
    """Synchronous-style helper (asyncio.run internally, matching this
    suite's established `run(coro)` pattern) for tests that just need a
    valid FK target for runs.project_id — not a full user/auth flow. Most
    call sites migrated from pre-multi-tenancy `db.create_run(run_id,
    task, ...)` just need *some* real project, not tenant-isolation
    behavior itself (that's what test_multi_tenancy.py exercises)."""
    import time

    from sqlalchemy import text

    from db.engine import get_sessionmaker

    async def _seed():
        async with get_sessionmaker()() as session:
            now = int(time.time())
            org_id = (await session.execute(
                text("INSERT INTO organizations (name, plan, gas_spent_wei, created_at) "
                     "VALUES (:name, 'free', 0, :now) RETURNING id"),
                {"name": f"{name} org", "now": now},
            )).scalar_one()
            project_id = (await session.execute(
                text("INSERT INTO projects (org_id, name, environment, created_at) "
                     "VALUES (:org_id, :name, 'live', :now) RETURNING id"),
                {"org_id": org_id, "name": name, "now": now},
            )).scalar_one()
            await session.commit()
            return project_id

    return asyncio.run(_seed())


def seed_user_and_token(email: str = "test@example.com", name: str = "Test User", email_verified: bool = True) -> dict:
    """Creates a real user (with its auto-provisioned org/project, see
    db.tenancy.provision_personal_org) and a matching JWT — for tests that
    call project-scoped endpoints through the `client` fixture and need a
    real `Authorization: Bearer <token>` header, not just a project_id.

    email_verified defaults True (Phase 4 G1's default is False for a
    real signup, but the overwhelming majority of this suite's existing
    callers need "a normal working member/admin/owner", not specifically
    an unverified one — defaulting True here keeps every pre-Phase-4 test
    exercising permissions.REQUIRES_VERIFIED_EMAIL-gated endpoints
    (member invites, API key creation) passing without each of them
    individually knowing this gate exists. Pass email_verified=False for
    the narrow tests that deliberately exercise the gate itself."""
    import auth
    import db

    async def _seed():
        user = await db.create_user(email=email, name=name, password="testpassword123", created_at=1_700_000_000)
        if email_verified:
            from sqlalchemy import update

            from db.engine import get_sessionmaker
            from db.models import User

            async with get_sessionmaker()() as session:
                await session.execute(update(User).where(User.id == user["userId"]).values(email_verified=True))
                await session.commit()
        token = auth.create_token(
            email=user["email"], name=user["name"],
            project_id=user["projectId"], org_id=user["orgId"], user_id=user["userId"],
        )
        return {**user, "token": token}

    return asyncio.run(_seed())


@pytest.fixture
def client():
    """FastAPI TestClient as a context manager, so app lifespan actually runs
    (bridge init fails gracefully without blockchain env vars — that's fine,
    it's non-fatal by design; see main.py's lifespan)."""
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as c:
        yield c


class _FakeAccount:
    address = "0xFakeWallet0000000000000000000000000000"


class _FakeEth:
    block_number = 123456
    chain_id = 10143


class _FakeW3:
    eth = _FakeEth()


class FakeBridge:
    """
    Stands in for BlockchainBridge in endpoint tests, so they never touch a
    real RPC/wallet regardless of what's in the local .env — deterministic
    everywhere, including CI with no blockchain credentials at all.
    """

    account = _FakeAccount()
    w3 = _FakeW3()

    async def get_run_audit_entries(self, run_id):
        return [{"entryId": 0, "runId": run_id, "agentId": "researcher", "action": "SEARCH"}]

    async def get_all_audit_entries(self):
        return [{"entryId": 0, "runId": "run_1", "agentId": "researcher", "action": "SEARCH"}]

    async def get_all_scores(self, run_id):
        return [{"agentId": "researcher", "runId": run_id, "score": 87}]

    async def get_all_score_histories(self, run_id):
        return {"researcher": [{"score": 87, "timestamp": 1000, "reason": "test"}]}

    async def get_leaderboard(self, max_runs=50):
        return {
            "agents": [{"agentId": "researcher", "avgScore": 87, "bestScore": 90, "runsCount": 3}],
            "totalRuns": 3,
            "runsConsidered": 3,
        }

    async def verify_run(self, run_id):
        return {"runId": run_id, "allMatch": True, "agents": []}

    async def tamper_demo(self, agent_id):
        if agent_id not in ("researcher", "validator", "scorer", "reporter"):
            raise ValueError(f"unknown agentId '{agent_id}'")
        return {
            "agentId": agent_id,
            "real": {"matches": True, "exists": True, "verified": True, "hash": "0xreal", "simulatedModel": "llama-3.3-70b-versatile"},
            "tampered": {"matches": False, "exists": True, "verified": False, "hash": "0xfake", "simulatedModel": "gpt-3.5-turbo"},
        }


@pytest.fixture
def client_with_fake_bridge(monkeypatch):
    """Same as `client`, but every endpoint's get_bridge() call returns a
    FakeBridge — use this for any test that exercises a bridge-backed route."""
    from fastapi.testclient import TestClient
    import main

    fake = FakeBridge()
    monkeypatch.setattr(main, "get_bridge", lambda: fake)

    with TestClient(main.app) as c:
        yield c


# ── unit/integration markers (plan's testing-pyramid categorization) ───────
#
# This suite is deliberately integration-heavy by design (see e.g.
# test_anchor_worker.py's/test_indexer.py's own module docstrings on why
# real Postgres/Anvil beats mocking them) — most tests genuinely ARE
# integration tests, not unit tests mislabeled. Rather than hand-annotate
# 260+ existing test functions (high effort, easy to let drift out of
# date), `pytest_collection_modifyitems` below classifies every collected
# item automatically: any test whose body — OR whose test-module-local
# helper functions, OR whose requested fixtures — references something
# that talks to a real dependency (a live app via TestClient, a real DB
# session, a real chain connection, a real subprocess/Docker call,
# outbound HTTP) is "integration"; everything else is "unit". Checking
# only the test function's own source isn't enough: a test like
# test_row_level_security.py's test_no_context_set_sees_zero_rows calls
# a local `_seed_two_projects()` helper (which touches real Postgres)
# and takes a real-Postgres-connecting `api_role_session_factory`
# fixture — neither of those strings appear in the TEST's own body, only
# in the helper/fixture it uses — found by this classifier's own
# verification pass initially misclassifying exactly that test (and
# test_db.py's test_run_lifecycle, which similarly only calls
# `db.create_user(...)`-style module helpers) as unit.
_INTEGRATION_SOURCE_MARKERS = (
    "TestClient", "get_sessionmaker", "get_engine", "requires_anvil",
    "chain_settings", "seed_project", "seed_user_and_token", "docker",
    "subprocess", "Web3(Web3.HTTPProvider", "Web3(HTTPProvider",
    "Web3(FallbackHTTPProvider", "Web3(provider", "httpx.", "requests.",
    "redis_client", "get_redis", "run_once", "client_with_fake_bridge",
    "db.", "agents.base", "log_step", "anchor_worker.", "indexer.",
    "blockchain.client", "score_writer", "identity_writer", "tenancy.",
    "create_async_engine", "session_factory", "get_audit_log_contract",
    "get_signer", "get_w3", "claim_batch", "build_batches", "submit_batch",
    "poll_once", "reconcile_batch_anchored", "index_score_updated",
    "reap_stale_claims", "is_password_pwned", "NonceAuthorityLock",
)
# Checked separately (word-boundary-ish) rather than folded into the plain
# substring list above: "client." alone is too generic (matches inside
# ordinary prose like "boto3's KMS client." in a docstring) to be a
# reliable substring marker even after comment/docstring stripping still
# leaves easy false positives — require it followed by a real attribute
# access pattern most consistent with the `client` pytest fixture.
_INTEGRATION_REGEX_MARKERS = (re.compile(r"\bclient\.(get|post|put|delete|patch)\("),)


def _strip_comments_and_docstrings(source: str) -> str:
    """Drop `# ...` line comments AND `\"\"\"...\"\"\"`/`'''...'''` triple-
    quoted strings before keyword matching — a docstring explaining
    unrelated code (e.g. "Stands in for boto3's KMS client." in
    test_kms_signer.py's FakeAwsKmsClient) shouldn't count as this test
    itself touching a real client any more than a `#` comment should
    (test_hashing_utils.py's test_compute_hash_format hit exactly that
    class of false positive via a `#` comment first; test_kms_signer.py's
    tests hit the docstring version during this classifier's own
    verification). Not a full tokenizer — a `#`/triple-quote appearing
    inside a *single*-quoted string literal would still survive, and any
    single- or double-quoted string content is left alone; acceptable
    here since a false negative (missing a real dependency mentioned only
    in a short string literal) is far less likely in this codebase's
    style than the false positives this exists to fix."""
    no_comments = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    return re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", no_comments)


def _module_helper_source(module, seed_source: str) -> str:
    """Source of only the module-local, non-test helper functions
    TRANSITIVELY reachable from `seed_source` (a test's own body, or a
    helper already pulled in) — not every helper defined anywhere in the
    file. Scanning every helper unconditionally over-corrects the other
    way: test_resilient_provider.py's pure CircuitBreaker tests never
    call `_anvil_is_up()`, but early versions of this classifier still
    pulled its real `httpx.post(...)` call into their scan just because
    it happened to live in the same file, misclassifying them as
    integration too (found during this classifier's own verification,
    same file as the test_kms_signer.py docstring false positive above —
    both directions of the same "which helpers actually apply to THIS
    test" problem). A test_* function is never itself pulled in as a
    "helper" of another test — vars(module) lists every test in the
    file, not just its private helpers."""
    import inspect

    candidates = {}
    for name in vars(module):
        if name.startswith(("__", "test_")):
            continue
        obj = getattr(module, name, None)
        if inspect.isfunction(obj) and getattr(obj, "__module__", None) == module.__name__:
            try:
                candidates[name] = inspect.getsource(obj)
            except (TypeError, OSError):
                pass

    included: dict[str, str] = {}
    frontier = seed_source
    changed = True
    while changed:
        changed = False
        for name, src in candidates.items():
            if name in included:
                continue
            if re.search(rf"\b{re.escape(name)}\s*\(", frontier):
                included[name] = src
                frontier += "\n" + src
                changed = True
    return "\n".join(included.values())


# autouse=True in this file — applied to literally every test regardless
# of whether IT touches Postgres/Redis, so including their source in the
# scan would (and, before this exclusion existed, did) pull the entire
# suite to "integration" including genuinely pure tests. Explicit
# (non-autouse) fixtures like `client`/`chain_settings`/a test-module-
# local `api_role_session_factory` are still scanned normally — a test
# only picks those up by actually requesting them.
_AMBIENT_AUTOUSE_FIXTURES = {"isolated_db", "_schema"}


def _fixture_source(item) -> str:
    """Source of every non-ambient fixture this test requests (skipping
    pytest/pluggy built-ins like monkeypatch/capsys/tmp_path, and the
    autouse fixtures above) — a fixture like api_role_session_factory
    (tests/test_row_level_security.py) is where the real Postgres
    connection actually happens, not in the test body that merely
    receives it as a parameter."""
    import inspect

    chunks = []
    for name, fixturedef in item._fixtureinfo.name2fixturedefs.items():
        if name in _AMBIENT_AUTOUSE_FIXTURES:
            continue
        for definition in fixturedef:
            func = definition.func
            if getattr(func, "__module__", "").startswith(("_pytest", "pluggy")):
                continue
            try:
                chunks.append(inspect.getsource(func))
            except (TypeError, OSError):
                pass
    return "\n".join(chunks)


def pytest_collection_modifyitems(config, items):
    import inspect

    for item in items:
        try:
            own_source = inspect.getsource(item.function)
        except (TypeError, OSError):
            own_source = ""
        try:
            helper_source = _module_helper_source(item.module, own_source)
        except Exception:
            helper_source = ""
        try:
            fixture_source = _fixture_source(item)
        except Exception:
            fixture_source = ""

        combined = _strip_comments_and_docstrings(own_source + "\n" + helper_source + "\n" + fixture_source)
        is_integration = (
            any(marker in combined for marker in _INTEGRATION_SOURCE_MARKERS)
            or any(regex.search(combined) for regex in _INTEGRATION_REGEX_MARKERS)
        )
        item.add_marker(pytest.mark.integration if is_integration else pytest.mark.unit)
