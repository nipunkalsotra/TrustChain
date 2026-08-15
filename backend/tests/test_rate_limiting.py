"""
Real-Redis tests for rate_limit.py (token-bucket + login backoff) and
db/idempotency.py, exercised through the actual HTTP endpoints — not just
the underlying functions in isolation, so FastAPI wiring (headers,
dependency order, status codes) is verified too.
"""

import asyncio

import db
from tests.conftest import seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _fake_run_pipeline(task, run_id=None, bridge=None):
    yield {"type": "run_started", "runId": run_id, "task": task}
    yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}


# ── POST /run-agent rate limiting ───────────────────────────────────────

def test_run_agent_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)
    # Small, deterministic bucket so the test doesn't depend on the
    # production default (5/min) or need to wait out a real refill window.
    import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "run_agent_rate_limit_capacity", 2)
    monkeypatch.setattr(settings, "run_agent_rate_limit_refill_per_second", 0.001)  # effectively no refill mid-test

    user = seed_user_and_token()
    ok1 = client.post("/run-agent", json={"task": "task 1"}, headers=_auth_headers(user["token"]))
    ok2 = client.post("/run-agent", json={"task": "task 2"}, headers=_auth_headers(user["token"]))
    limited = client.post("/run-agent", json={"task": "task 3"}, headers=_auth_headers(user["token"]))

    assert ok1.status_code == 200
    assert ok2.status_code == 200
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_run_agent_rate_limit_is_isolated_per_project(client, monkeypatch):
    """Alice exhausting her bucket must not affect Bob's."""
    import config
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)
    settings = config.get_settings()
    monkeypatch.setattr(settings, "run_agent_rate_limit_capacity", 1)
    monkeypatch.setattr(settings, "run_agent_rate_limit_refill_per_second", 0.001)

    alice = seed_user_and_token("alice-ratelimit@example.com", "Alice")
    bob = seed_user_and_token("bob-ratelimit@example.com", "Bob")

    ok = client.post("/run-agent", json={"task": "t"}, headers=_auth_headers(alice["token"]))
    limited = client.post("/run-agent", json={"task": "t"}, headers=_auth_headers(alice["token"]))
    bob_ok = client.post("/run-agent", json={"task": "t"}, headers=_auth_headers(bob["token"]))

    assert ok.status_code == 200
    assert limited.status_code == 429
    assert bob_ok.status_code == 200


# ── POST /agents rate limiting — register_agent spends real gas on every
#    call, same class of concern as /run-agent, and had NO rate limit at
#    all before this ──────────────────────────────────────────────────

def test_register_agent_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    import config
    import uuid

    settings = config.get_settings()
    monkeypatch.setattr(settings, "register_agent_rate_limit_capacity", 2)
    monkeypatch.setattr(settings, "register_agent_rate_limit_refill_per_second", 0.001)

    async def _fake_register_agent(project_id, agent_id, code_hash, model, version):
        return "0x" + "cd" * 32

    import main
    monkeypatch.setattr(main.identity_writer, "register_agent", _fake_register_agent)

    user = seed_user_and_token()
    headers = _auth_headers(user["token"])

    def _body():
        return {
            "agent_id": f"rl_test_agent_{uuid.uuid4().hex[:8]}",
            "code_hash": "0x" + "ab" * 32, "model": "gpt-4o", "version": "v1",
        }

    ok1 = client.post("/agents", json=_body(), headers=headers)
    ok2 = client.post("/agents", json=_body(), headers=headers)
    limited = client.post("/agents", json=_body(), headers=headers)

    assert ok1.status_code == 200, ok1.text
    assert ok2.status_code == 200, ok2.text
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert limited.json()["error_code"] == "rate_limit_exceeded"


# ── POST /steps rate limiting — unbounded step-logging is unbounded
#    Postgres/outbox write volume for free, even though it doesn't spend
#    gas synchronously the way /run-agent and /agents do ────────────────

def test_log_step_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    import config
    import uuid

    settings = config.get_settings()
    monkeypatch.setattr(settings, "log_step_rate_limit_capacity", 2)
    monkeypatch.setattr(settings, "log_step_rate_limit_refill_per_second", 0.001)

    user = seed_user_and_token()
    headers = _auth_headers(user["token"])
    run_id = f"rl_step_test_{uuid.uuid4().hex[:8]}"

    def _body():
        return {"run_id": run_id, "agent_id": "a", "action": "b", "input": "i", "output": "o"}

    ok1 = client.post("/steps", json=_body(), headers=headers)
    ok2 = client.post("/steps", json=_body(), headers=headers)
    limited = client.post("/steps", json=_body(), headers=headers)

    assert ok1.status_code == 200, ok1.text
    assert ok2.status_code == 200, ok2.text
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert limited.json()["error_code"] == "rate_limit_exceeded"


# ── POST /auth/login credential-stuffing backoff ────────────────────────

def test_login_backoff_locks_out_after_repeated_failures(client):
    user = seed_user_and_token("backoff-target@example.com", "Target")

    statuses = []
    for _ in range(6):
        r = client.post("/auth/login", json={"email": user["email"], "password": "wrong-password"})
        statuses.append(r.status_code)

    # Early failures are plain 401s; enough repeated failures within the
    # backoff window eventually trip the 429 lockout.
    assert statuses[0] == 401
    assert 429 in statuses
    assert "Retry-After" in client.post(
        "/auth/login", json={"email": user["email"], "password": "wrong-password"}
    ).headers


def test_login_backoff_is_per_account_not_just_per_ip():
    """The account dimension of backoff is isolated per-email: hammering
    one account's login shouldn't lock out a DIFFERENT account, holding
    IP constant. (The IP dimension is deliberately the opposite — many
    failures from one source SHOULD eventually rate-limit that source
    regardless of which account it's hitting, which is what makes it a
    useful defense against credential stuffing across many accounts from
    one attacker. That can't be exercised through TestClient, which has
    no way to vary the apparent source IP per call, so this test drives
    rate_limit.py directly with two distinct IPs to isolate the account
    dimension specifically.)"""
    import asyncio

    from fastapi import HTTPException

    import rate_limit

    async def _hammer():
        for _ in range(6):
            try:
                await rate_limit.check_login_backoff("victim@example.com", "10.0.0.1")
            except HTTPException:
                pass  # expected once victim's account trips its own lockout
            await rate_limit.record_login_failure("victim@example.com", "10.0.0.1")

    asyncio.run(_hammer())

    # A different account, from a different IP, must be unaffected.
    asyncio.run(rate_limit.check_login_backoff("bystander@example.com", "10.0.0.2"))


def test_successful_login_clears_backoff(client):
    user = seed_user_and_token("recovers@example.com", "Recovers")

    client.post("/auth/login", json={"email": user["email"], "password": "wrong-password"})
    ok = client.post("/auth/login", json={"email": user["email"], "password": "testpassword123"})
    assert ok.status_code == 200

    # After a successful login, failure counters are cleared — the very
    # next wrong-password attempt is a fresh 401, not still counting up
    # toward a lockout from before.
    r = client.post("/auth/login", json={"email": user["email"], "password": "wrong-password"})
    assert r.status_code == 401


# ── Idempotency-Key on POST /run-agent ──────────────────────────────────

def test_idempotency_key_replays_the_same_response_without_rerunning(client, monkeypatch):
    import main

    call_count = 0

    async def _counting_pipeline(task, run_id=None, bridge=None):
        nonlocal call_count
        call_count += 1
        yield {"type": "run_started", "runId": run_id, "task": task}
        yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}

    monkeypatch.setattr(main, "run_pipeline", _counting_pipeline)

    user = seed_user_and_token()
    headers = {**_auth_headers(user["token"]), "Idempotency-Key": "fixed-key-123"}

    r1 = client.post("/run-agent", json={"task": "same task"}, headers=headers)
    r2 = client.post("/run-agent", json={"task": "same task"}, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()  # identical run_id, not a second run

    # only ONE run row was actually created — the second call replayed
    # the cached response instead of re-running db.create_run()
    runs = asyncio.run(db.list_runs(user["projectId"], limit=10))
    assert len(runs) == 1


def test_idempotency_key_reused_with_different_body_is_conflict(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)

    user = seed_user_and_token()
    headers = {**_auth_headers(user["token"]), "Idempotency-Key": "reused-key"}

    r1 = client.post("/run-agent", json={"task": "task A"}, headers=headers)
    r2 = client.post("/run-agent", json={"task": "a genuinely different task"}, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 409


def test_idempotency_key_is_scoped_per_project(client, monkeypatch):
    """Two different tenants coincidentally using the same key string must
    not collide — each gets its own run."""
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)

    alice = seed_user_and_token("alice-idem@example.com", "Alice")
    bob = seed_user_and_token("bob-idem@example.com", "Bob")
    headers_common_key = "shared-key-value"

    r1 = client.post(
        "/run-agent", json={"task": "alice task"},
        headers={**_auth_headers(alice["token"]), "Idempotency-Key": headers_common_key},
    )
    r2 = client.post(
        "/run-agent", json={"task": "bob task"},
        headers={**_auth_headers(bob["token"]), "Idempotency-Key": headers_common_key},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["run_id"] != r2.json()["run_id"]
