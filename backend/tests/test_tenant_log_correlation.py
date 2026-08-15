"""
tests/test_tenant_log_correlation.py — F11 ("Structured logging with
correlation IDs ... request_id, run_id, tenant_id"): every log line
emitted while handling an authenticated request must carry the caller's
tenant identity (project_id/org_id — this codebase's actual tenant unit,
see logging_config.py's bind_tenant_context docstring for why those are
the field names rather than a literal "tenant_id").
"""

import logging

from tests.conftest import FakeBridge, seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_correlation_ids_processor_includes_project_and_org_id():
    """Direct check of the structlog processor itself: once
    bind_tenant_context() has been called (as auth.py's dependencies do
    for every authenticated request), _add_correlation_ids must inject
    project_id/org_id into the event dict, matching the same mechanism
    already relied on for request_id/run_id."""
    import logging_config

    logging_config.bind_tenant_context(42, 7)
    logging_config.bind_run_id("run_abc123")

    event_dict = logging_config._add_correlation_ids(None, "info", {"event": "something_happened"})

    assert event_dict["project_id"] == 42
    assert event_dict["org_id"] == 7
    assert event_dict["run_id"] == "run_abc123"


def test_correlation_ids_processor_omits_unset_tenant_fields():
    """A log line emitted with no tenant context bound (e.g. before any
    request has been handled in this task) shouldn't carry stale or
    fabricated project_id/org_id keys."""
    import logging_config

    logging_config.bind_tenant_context(None, None)

    event_dict = logging_config._add_correlation_ids(None, "info", {"event": "no_tenant_yet"})

    assert "project_id" not in event_dict
    assert "org_id" not in event_dict


def test_real_authenticated_request_logs_carry_org_id(client, caplog, monkeypatch):
    """End-to-end: a real authenticated HTTP request through the actual
    FastAPI app must produce a real log line (captured via caplog, which
    structlog's stdlib.LoggerFactory integration routes through like any
    other Python logging call) carrying that request's real org_id — not
    a reimplementation of the binding logic, the actual auth.py
    dependency running for a real request.

    Checks org_id specifically (not project_id): main.py's own
    "run_started" log line already passes project_id explicitly as a
    kwarg, so seeing it there wouldn't prove THIS processor added it.
    org_id is never passed explicitly by that call site — if it shows up
    at all, it can only have come from bind_tenant_context's contextvar,
    which is exactly the mechanism under test."""

    async def _fake_run_pipeline(task, run_id=None, bridge=None):
        yield {"type": "run_started", "runId": run_id, "task": task}
        yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}

    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)

    user = seed_user_and_token("tenant-log-test@example.com", "TenantLogTest")

    with caplog.at_level(logging.INFO):
        r = client.post("/run-agent", json={"task": "tenant log test task"}, headers=_auth_headers(user["token"]))
    assert r.status_code == 200, r.text

    found = False
    for record in caplog.records:
        message = record.getMessage()
        if "run_started" in message and f"'org_id': {user['orgId']}" in message:
            found = True
            break
    assert found, "no captured log line carried the real request's org_id"


def test_background_pipeline_task_inherits_tenant_context(monkeypatch, client):
    """POST /run-agent spawns its pipeline work via asyncio.create_task()
    from within the request handler — confirms that spawned task's
    context (a COPY taken at creation time, per asyncio semantics) still
    carries the tenant identity bound by auth.py's dependency, so logs
    from deep inside a run (agents/*, run_events, db writes) stay
    correlated to the tenant that started it, not just the top-level
    request log line."""
    import logging_config
    import main

    # get_bridge() runs INSIDE _run_pipeline_background's try block BEFORE
    # run_pipeline() — without a real PRIVATE_KEY/.env (true in CI, and
    # should stay true there) it raises immediately, so this test's fake
    # pipeline below would never run at all and `captured` would stay
    # empty. Same fix as test_shutdown_drain.py's tests.
    monkeypatch.setattr(main, "get_bridge", lambda: FakeBridge())

    captured = {}

    async def _capturing_pipeline(task, run_id=None, bridge=None):
        captured["project_id_during_run"] = logging_config._project_id_var.get()
        captured["org_id_during_run"] = logging_config._org_id_var.get()
        yield {"type": "run_started", "runId": run_id, "task": task}
        yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 0, "txHashes": []}

    monkeypatch.setattr(main, "run_pipeline", _capturing_pipeline)

    user = seed_user_and_token("tenant-log-bg-test@example.com", "TenantLogBg")
    r = client.post("/run-agent", json={"task": "bg tenant context task"}, headers=_auth_headers(user["token"]))
    assert r.status_code == 200, r.text

    assert captured["project_id_during_run"] == user["projectId"]
    assert captured["org_id_during_run"] == user["orgId"]
