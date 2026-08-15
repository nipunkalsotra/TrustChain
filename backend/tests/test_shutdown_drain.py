"""
tests/test_shutdown_drain.py — F14 ("Drain in-flight work on SIGTERM;
never abandon a submitted-but-unconfirmed transaction without recording
it") for the API process specifically: POST /run-agent's background
pipeline task is untracked by uvicorn's own graceful-shutdown machinery
(that only covers in-flight HTTP requests), so main.py's lifespan now
tracks it itself and, on shutdown, either lets it finish naturally within
api_shutdown_drain_timeout_seconds or explicitly records it as failed —
closing the "stuck at status='running' forever" gap a cancelled/abandoned
task would otherwise leave.

Drives the REAL ASGI lifespan shutdown sequence by opening and then
explicitly closing a TestClient's `with` block inside the test body
(rather than using the shared `client` fixture, whose teardown happens
after the test function already returned control to pytest) — not a
reimplementation of the drain logic.
"""

import asyncio
import time

import db
from tests.conftest import FakeBridge, seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_shutdown_force_fails_a_still_running_pipeline_task(monkeypatch):
    import config
    import main
    from fastapi.testclient import TestClient

    settings = config.get_settings()
    monkeypatch.setattr(settings, "api_shutdown_drain_timeout_seconds", 0.3)

    # _run_pipeline_background calls get_bridge() INSIDE its try block
    # BEFORE run_pipeline() — in CI (no real PRIVATE_KEY/.env configured
    # for V1's BlockchainBridge) that raises immediately, so the run
    # fails with "PRIVATE_KEY not set in .env" before this test's fake
    # run_pipeline is ever reached, never actually exercising the slow/
    # in-flight path this test means to cover. Stub it the same way
    # conftest.py's client_with_fake_bridge fixture does, so this is
    # deterministic everywhere, matching main.py's own documented lesson
    # about this exact class of local-vs-CI divergence (see
    # _run_pipeline_background's docstring).
    monkeypatch.setattr(main, "get_bridge", lambda: FakeBridge())

    async def _slow_pipeline(task, run_id=None, bridge=None):
        yield {"type": "run_started", "runId": run_id, "task": task}
        await asyncio.sleep(5)  # far longer than the 0.3s drain timeout above
        yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}

    monkeypatch.setattr(main, "run_pipeline", _slow_pipeline)

    user = seed_user_and_token("shutdown-slow@example.com", "ShutdownSlow")
    with TestClient(main.app) as client:
        r = client.post("/run-agent", json={"task": "slow task"}, headers=_auth_headers(user["token"]))
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
    # TestClient's `with` block has now exited -> real ASGI shutdown ran ->
    # lifespan's drain waited 0.3s (the pipeline task is nowhere near done)
    # -> the task was force-failed before the process would have exited.

    run = asyncio.run(db.get_run(run_id, user["projectId"]))
    assert run is not None
    assert run["status"] == "error"
    assert "shutdown" in run["result"]["message"].lower()


def test_shutdown_does_not_clobber_a_run_that_finishes_within_the_drain_window(monkeypatch):
    import config
    import main
    from fastapi.testclient import TestClient

    settings = config.get_settings()
    monkeypatch.setattr(settings, "api_shutdown_drain_timeout_seconds", 5.0)
    monkeypatch.setattr(main, "get_bridge", lambda: FakeBridge())

    async def _fast_pipeline(task, run_id=None, bridge=None):
        yield {"type": "run_started", "runId": run_id, "task": task}
        yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}

    monkeypatch.setattr(main, "run_pipeline", _fast_pipeline)

    user = seed_user_and_token("shutdown-fast@example.com", "ShutdownFast")
    with TestClient(main.app) as client:
        r = client.post("/run-agent", json={"task": "fast task"}, headers=_auth_headers(user["token"]))
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
    # The task finishes almost immediately — well inside the 5s drain
    # window — so shutdown should observe it complete naturally, not force-
    # fail it.

    run = asyncio.run(db.get_run(run_id, user["projectId"]))
    assert run is not None
    assert run["status"] == "complete"


def test_fail_run_if_still_running_does_not_overwrite_an_already_terminal_run():
    """Direct unit-level check of the guard db.fail_run_if_still_running
    relies on — a run already marked complete/error must stay that way,
    confirmed independent of the full shutdown flow above."""
    user = seed_user_and_token("shutdown-guard@example.com", "ShutdownGuard")
    run_id = f"shutdown_guard_test_{int(time.time() * 1000)}"

    async def _seed_and_complete():
        await db.create_run(run_id, project_id=user["projectId"], task="t", user_email=None, created_at=int(time.time()))
        await db.complete_run(run_id, {"report": "already done"}, int(time.time()))
        changed = await db.fail_run_if_still_running(run_id, "should not apply", int(time.time()))
        return changed

    changed = asyncio.run(_seed_and_complete())
    assert changed is False

    run = asyncio.run(db.get_run(run_id, project_id=user["projectId"]))
    assert run["status"] == "complete"
