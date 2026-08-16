import asyncio

import db
from tests.conftest import seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── observability plumbing ───────────────────────────────────────────────────

def test_ready_reports_database_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"]["database"]["ok"] is True


def test_ready_does_not_leak_raw_exception_details(client, monkeypatch):
    # F12: /ready has no auth (an orchestrator's health-checker doesn't
    # carry a bearer token), so a failing check's raw exception message
    # must never reach the response body — it could contain a Postgres
    # DSN with credentials, or an RPC URL with an embedded API key. The
    # real error still goes to the server-side log (logger.error), just
    # not to the caller.
    import main

    # Not a real credential shape (deliberately — gitleaks' own
    # stripe-access-token rule flagged an earlier `sk_live_...`-prefixed
    # version of this fixture as a real leaked secret and failed CI; the
    # test only needs *some* string that must not reach the response, not
    # one that happens to pattern-match a specific provider's key format).
    canary = "this-must-never-reach-the-response-body-9f8e7d6c5b4a3210"

    def _broken_bridge():
        raise ConnectionError(f"simulated outage carrying a sensitive value: {canary}")

    monkeypatch.setattr(main, "get_bridge", _broken_bridge)

    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["chain"] == {"ok": False}
    assert canary not in r.text
    assert "error" not in body["checks"]["chain"]


def test_ready_reports_migrations_ok_when_current(client):
    # F15. Exercised for real: the test suite's own DB (Testcontainers-
    # provisioned, see tests/conftest.py — real `alembic upgrade head` run
    # against it) genuinely has alembic_version == this checkout's head.
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["migrations"] == {"ok": True}


def test_ready_flips_not_ready_on_a_real_migration_version_mismatch(client):
    # F15: a running process whose applied schema is behind what its own
    # code expects isn't safe to route traffic to — unlike the chain
    # check, this one blocks readiness. Manipulates the REAL
    # alembic_version row (not a mock) and restores it afterward —
    # alembic_version isn't in truncate_all_tables()'s table list, so a
    # leftover bogus value would otherwise leak into later tests.
    from sqlalchemy import text

    from db.engine import get_sessionmaker

    async def _set_version(value: str):
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": value})
            await session.commit()

    async def _get_version() -> str:
        async with get_sessionmaker()() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar_one()

    original = asyncio.run(_get_version())
    try:
        asyncio.run(_set_version("000000000000"))  # alembic_version.version_num is varchar(32) — a plausible-length but definitely-wrong hash
        r = client.get("/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["checks"]["migrations"] == {"ok": False}
        assert body["ready"] is False
    finally:
        asyncio.run(_set_version(original))


def test_ready_treats_missing_alembic_version_table_as_non_blocking(client, monkeypatch):
    # No alembic_version table at all (a schema built straight from
    # models, e.g. via create_all_tables() with no migration ever run) is
    # a real, expected test/dev state, not a sign anything's wrong —
    # informational only, doesn't flip readiness. Real code path
    # exercised (db.get_applied_migration_version's own ProgrammingError
    # handling), just via a monkeypatched return rather than actually
    # dropping the table mid-suite (which other tests' fixtures depend on).
    import db

    async def _fake_none():
        return None

    monkeypatch.setattr(db, "get_applied_migration_version", _fake_none)

    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["migrations"] == {"ok": True}
    assert body["ready"] is True


def test_ready_does_not_require_chain(client, monkeypatch):
    # Force the chain check to fail regardless of ambient .env state (a dev
    # machine with real credentials would otherwise make this test flaky —
    # it needs to assert the *policy* — chain failures don't flip
    # readiness — not depend on whichever credentials happen to be present).
    import main

    def _broken_bridge():
        raise ConnectionError("simulated RPC outage")

    monkeypatch.setattr(main, "get_bridge", _broken_bridge)

    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True
    assert r.json()["checks"]["chain"]["ok"] is False


def test_correlation_id_echoed_on_every_response(client):
    user = seed_user_and_token()
    r = client.get("/runs", headers=_auth_headers(user["token"]))
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) > 0


def test_correlation_id_passthrough_when_client_supplies_one(client):
    user = seed_user_and_token()
    r = client.get("/runs", headers={**_auth_headers(user["token"]), "X-Request-ID": "my-custom-id"})
    assert r.headers["X-Request-ID"] == "my-custom-id"


# ── /runs — project-scoped (Phase 2.3 multi-tenancy, invariant I7) ─────────

def test_runs_requires_auth(client):
    assert client.get("/runs").status_code == 401
    assert client.get("/runs/does-not-exist").status_code == 401


def test_get_run_not_found(client):
    user = seed_user_and_token()
    r = client.get("/runs/does-not-exist", headers=_auth_headers(user["token"]))
    assert r.status_code == 404


def test_list_runs_empty(client):
    user = seed_user_and_token()
    r = client.get("/runs", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    assert r.json() == {"runs": [], "total": 0}


def test_list_and_get_run_after_persisting(client):
    user = seed_user_and_token()
    asyncio.run(db.create_run("run_1", user["projectId"], "do the thing", user["email"], 1000))
    asyncio.run(db.complete_run("run_1", {"type": "run_complete", "score": 91}, 1100))

    r = client.get("/runs", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r2 = client.get("/runs/run_1", headers=_auth_headers(user["token"]))
    assert r2.status_code == 200
    assert r2.json() == {"type": "run_complete", "score": 91}


def test_get_run_still_running_is_404(client):
    user = seed_user_and_token()
    asyncio.run(db.create_run("run_2", user["projectId"], "in progress", None, 1000))

    r = client.get("/runs/run_2", headers=_auth_headers(user["token"]))
    assert r.status_code == 404


def test_runs_are_isolated_between_tenants(client):
    """Invariant I7: Bob must not be able to list or read Alice's runs,
    even though both are authenticated, and even by guessing a real run_id."""
    alice = seed_user_and_token("alice@example.com", "Alice")
    bob = seed_user_and_token("bob@example.com", "Bob")
    asyncio.run(db.create_run("run_alice", alice["projectId"], "alice task", alice["email"], 1000))
    asyncio.run(db.complete_run("run_alice", {"type": "run_complete", "score": 91}, 1100))

    r = client.get("/runs", headers=_auth_headers(bob["token"]))
    assert r.json() == {"runs": [], "total": 0}

    r2 = client.get("/runs/run_alice", headers=_auth_headers(bob["token"]))
    assert r2.status_code == 404  # not "yours" — identical to nonexistent, not a 403

    r3 = client.get("/runs/run_alice", headers=_auth_headers(alice["token"]))
    assert r3.status_code == 200


# ── /audit-log, /trust-scores, /leaderboard — pure Postgres read model,
#    no bridge involved (see db/read_model.py), project-scoped ────────────

def _insert_score(agent_id: str, run_id: str, score: int, reason: str, ts: int, row_id_hint: int):
    from db.engine import get_sessionmaker
    from db.models import ReadModelScore

    async def _insert():
        async with get_sessionmaker()() as session:
            session.add(ReadModelScore(
                agent_id=agent_id, run_id=run_id, score=score, reason=reason,
                timestamp=ts, block_number=row_id_hint, tx_hash=f"0x{row_id_hint:064x}",
                log_index=0, indexed_at=ts,
            ))
            await session.commit()

    asyncio.run(_insert())


def test_trust_scores(client):
    user = seed_user_and_token()
    asyncio.run(db.create_run("run_1", user["projectId"], "task", None, 1000))
    _insert_score("researcher", "run_1", 87, "pipeline_scoring", 1000, 1)

    r = client.get("/trust-scores?run_id=run_1", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    assert r.json()["scores"][0]["agentId"] == "researcher"
    assert r.json()["scores"][0]["score"] == 87


def test_trust_scores_returns_latest_not_first(client):
    """A rescored agent's history has >1 row for the same run — the
    endpoint must surface the most recent score, not the first one."""
    user = seed_user_and_token()
    asyncio.run(db.create_run("run_rescored", user["projectId"], "task", None, 1000))
    _insert_score("researcher", "run_rescored", 50, "first_pass", 1000, 1)
    _insert_score("researcher", "run_rescored", 92, "rescored", 1010, 2)

    r = client.get("/trust-scores?run_id=run_rescored", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    scores = r.json()["scores"]
    assert len(scores) == 1
    assert scores[0]["score"] == 92


def test_trust_scores_are_isolated_between_tenants(client):
    alice = seed_user_and_token("alice2@example.com", "Alice")
    bob = seed_user_and_token("bob2@example.com", "Bob")
    asyncio.run(db.create_run("run_alice_scores", alice["projectId"], "task", None, 1000))
    _insert_score("researcher", "run_alice_scores", 99, "pipeline_scoring", 1000, 1)

    r = client.get("/trust-scores?run_id=run_alice_scores", headers=_auth_headers(bob["token"]))
    assert r.status_code == 200
    assert r.json()["scores"] == []  # not 403 — a foreign run_id looks identical to a nonexistent one


def test_audit_log_all(client):
    import time

    from agents.base import log_step

    user = seed_user_and_token()
    asyncio.run(db.create_run("run_audit", user["projectId"], "task", None, int(time.time())))
    asyncio.run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="hi", output_text="there", step_index=0, run_id="run_audit",
    ))

    r = client.get("/audit-log", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    entry = body["entries"][0]
    assert entry["agentId"] == "researcher"
    assert entry["runId"] == "run_audit"
    assert entry["txHash"].startswith("pending:")   # not yet anchored — no worker running in this test
    assert entry["anchorStatus"] == "pending"


def test_audit_log_reflects_confirmed_anchor(client):
    """Once a step's batch is confirmed, /audit-log must surface the real
    tx_hash and anchorStatus='confirmed' — not the pending placeholder.
    Builds the AnchorBatch row directly (no live worker/chain needed here;
    the worker's actual anchoring is covered end-to-end by
    tests/test_anchor_worker.py) to test read_model.py's join logic in
    isolation."""
    import time

    from agents.base import log_step
    from db.engine import get_sessionmaker
    from db.models import AnchorBatch, Step

    user = seed_user_and_token()
    asyncio.run(db.create_run("run_confirmed", user["projectId"], "task", None, int(time.time())))
    _, evt = asyncio.run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="hi", output_text="there", step_index=0, run_id="run_confirmed",
    ))

    async def _confirm_batch():
        async with get_sessionmaker()() as session:
            batch = AnchorBatch(
                run_id_hash="0x" + "ab" * 32, merkle_root="0x" + "cd" * 32, step_count=1,
                leaf_order=[evt["stepId"]], status="confirmed", tx_hash="0x" + "ef" * 32,
                block_number=42, created_at=int(time.time()), confirmed_at=int(time.time()),
            )
            session.add(batch)
            await session.flush()
            step = await session.get(Step, evt["stepId"])
            step.anchor_batch_id = batch.id
            await session.commit()

    asyncio.run(_confirm_batch())

    r = client.get("/audit-log?run_id=run_confirmed", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    entry = r.json()["entries"][0]
    assert entry["anchorStatus"] == "confirmed"
    assert entry["txHash"] == "0x" + "ef" * 32


def test_audit_log_isolated_between_tenants(client):
    import time

    from agents.base import log_step

    alice = seed_user_and_token("alice3@example.com", "Alice")
    bob = seed_user_and_token("bob3@example.com", "Bob")
    asyncio.run(db.create_run("run_alice_audit", alice["projectId"], "task", None, int(time.time())))
    asyncio.run(log_step(
        bridge=None, agent_id="researcher", action="task_received",
        input_text="hi", output_text="there", step_index=0, run_id="run_alice_audit",
    ))

    r = client.get("/audit-log", headers=_auth_headers(bob["token"]))
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_leaderboard(client):
    user = seed_user_and_token()
    for i, (run_id, score) in enumerate([("run_a", 80), ("run_b", 90), ("run_c", 70)]):
        asyncio.run(db.create_run(run_id, user["projectId"], "task", None, 1000 + i))
        _insert_score("researcher", run_id, score, "pipeline_scoring", 1000 + i, i + 1)

    r = client.get("/leaderboard", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["totalRuns"] == 3
    assert body["agents"][0]["agentId"] == "researcher"
    assert body["agents"][0]["avgScore"] == 80
    assert body["agents"][0]["bestScore"] == 90
    assert body["agents"][0]["runsCount"] == 3


def test_leaderboard_is_isolated_between_tenants(client):
    alice = seed_user_and_token("alice4@example.com", "Alice")
    bob = seed_user_and_token("bob4@example.com", "Bob")
    asyncio.run(db.create_run("run_alice_lb", alice["projectId"], "task", None, 1000))
    _insert_score("researcher", "run_alice_lb", 95, "pipeline_scoring", 1000, 1)

    r = client.get("/leaderboard", headers=_auth_headers(bob["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["totalRuns"] == 0
    assert body["agents"] == []


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


def test_internal_errors_do_not_leak_exception_details(client, monkeypatch):
    """F12 (Phase 2 plan's fix list): an unexpected failure must return a
    generic message, not str(e) — which could contain SQL text, internal
    paths, or other implementation detail a client has no business
    seeing. The real message still reaches structured logs (server-side
    only), just not the HTTP response body."""
    import db.read_model as read_model

    sensitive_detail = "connection to postgresql://trustchain:s3cr3t@10.0.0.5/trustchain failed"

    async def _boom(*args, **kwargs):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(read_model, "get_leaderboard", _boom)

    user = seed_user_and_token()
    r = client.get("/leaderboard", headers=_auth_headers(user["token"]))
    assert r.status_code == 500
    assert sensitive_detail not in r.text
    assert r.json()["detail"] == "internal error — see server logs for details"


def test_error_responses_carry_a_stable_machine_readable_error_code(client):
    """Typed error taxonomy (errors.py) — every ApiError-raised response
    must carry error_code as a SIBLING of detail, not a replacement for
    it (F12's test above proves detail alone stays intact). Three
    different real failures, three different status codes, three
    different error_codes — this is exactly what a status-code-only
    client can't distinguish (all three ARE plausible 4xx "your request
    was bad" cases) but error_code can."""
    signup_body = {"email": "dupe_error_code_test@example.com", "name": "Test", "password": "correct horse battery staple"}

    first = client.post("/auth/signup", json=signup_body)
    assert first.status_code == 200

    dup = client.post("/auth/signup", json=signup_body)
    assert dup.status_code == 409
    assert dup.json()["error_code"] == "email_already_registered"

    bad_login = client.post("/auth/login", json={"email": signup_body["email"], "password": "wrong password entirely"})
    assert bad_login.status_code == 401
    assert bad_login.json()["error_code"] == "invalid_credentials"

    no_auth = client.get("/runs")
    assert no_auth.status_code == 401
    assert no_auth.json()["error_code"] == "missing_bearer_token"
