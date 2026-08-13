import db


# ── /runs — pure SQLite, no bridge involved ─────────────────────────────────

def test_get_run_not_found(client):
    r = client.get("/runs/does-not-exist")
    assert r.status_code == 404


def test_list_runs_empty(client):
    r = client.get("/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": [], "total": 0}


def test_list_and_get_run_after_persisting(client):
    import asyncio
    asyncio.run(db.create_run("run_1", "do the thing", "a@b.com", 1000))
    asyncio.run(db.complete_run("run_1", {"type": "run_complete", "score": 91}, 1100))

    r = client.get("/runs")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r2 = client.get("/runs/run_1")
    assert r2.status_code == 200
    assert r2.json() == {"type": "run_complete", "score": 91}


def test_get_run_still_running_is_404(client):
    import asyncio
    asyncio.run(db.create_run("run_2", "in progress", None, 1000))

    r = client.get("/runs/run_2")
    assert r.status_code == 404


# ── Bridge-backed endpoints — FakeBridge, never touches real RPC ───────────

def test_trust_scores(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/trust-scores?run_id=run_1")
    assert r.status_code == 200
    assert r.json()["scores"][0]["agentId"] == "researcher"


def test_audit_log_all(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/audit-log")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_leaderboard(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/leaderboard")
    assert r.status_code == 200
    body = r.json()
    assert body["totalRuns"] == 3
    assert body["agents"][0]["agentId"] == "researcher"


def test_verify(client_with_fake_bridge):
    r = client_with_fake_bridge.post("/verify", json={"runId": "run_1"})
    assert r.status_code == 200
    assert r.json()["allMatch"] is True


def test_tamper_demo_pass_case(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/verify/tamper-demo?agent_id=researcher")
    assert r.status_code == 200
    body = r.json()
    assert body["real"]["verified"] is True
    assert body["tampered"]["verified"] is False


def test_tamper_demo_unknown_agent_is_404(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/verify/tamper-demo?agent_id=not-a-real-agent")
    assert r.status_code == 404


def test_chain_status_connected(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/chain-status")
    assert r.status_code == 200
    assert r.json()["connected"] is True
    assert r.json()["chainId"] == 10143


def test_health(client_with_fake_bridge):
    r = client_with_fake_bridge.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
