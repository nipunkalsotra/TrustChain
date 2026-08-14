import asyncio
import os

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
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trustchain:trustchain@localhost:5432/trustchain",
)

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


def seed_user_and_token(email: str = "test@example.com", name: str = "Test User") -> dict:
    """Creates a real user (with its auto-provisioned org/project, see
    db.tenancy.provision_personal_org) and a matching JWT — for tests that
    call project-scoped endpoints through the `client` fixture and need a
    real `Authorization: Bearer <token>` header, not just a project_id."""
    import auth
    import db

    async def _seed():
        user = await db.create_user(email=email, name=name, password="testpassword123", created_at=1_700_000_000)
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
