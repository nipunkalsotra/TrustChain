import pytest

import db as db_module


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file — never touches trustchain.db."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_conn", None)
    yield
    monkeypatch.setattr(db_module, "_conn", None)


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
