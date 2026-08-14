"""
End-to-end test for POST /run-agent -> GET /stream/{run_id}, backed by
Redis Streams (run_events.py) instead of the old in-process
`_run_queues` dict. Real Redis (docker-compose's `redis` service) — this
is exactly what a mock wouldn't verify: that a second process (or, in a
test, a client reading well after the background task started publishing)
still sees the full event history via a real Redis connection.
"""

import json

import pytest
import redis.exceptions

from tests.conftest import seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _fake_run_pipeline(task, run_id=None, bridge=None):
    yield {"type": "run_started", "runId": run_id, "task": task}
    yield {
        "agentId": "researcher", "action": "task_received", "txHash": "pending:1",
        "step": 0, "inputHash": "0x00", "outputHash": "0x00", "trustScore": 0,
        "runId": run_id, "timestamp": 1000,
    }
    yield {"type": "run_complete", "runId": run_id, "report": "done", "score": 91, "txCount": 1, "txHashes": []}


async def _fake_run_pipeline_that_errors_internally(task, run_id=None, bridge=None):
    """Mirrors agents/pipeline.py's OWN behavior: it catches its own
    internal exceptions (any LangGraph node failing) and yields a normal
    {"type": "error", ...} event instead of letting the exception
    propagate — the generator then returns NORMALLY, no exception raised
    to the caller. See _run_pipeline_background's docstring in main.py for
    why that distinction matters."""
    yield {"type": "run_started", "runId": run_id, "task": task}
    yield {"type": "error", "runId": run_id, "message": "simulated internal pipeline failure"}


def test_run_agent_then_stream_sees_all_events_via_redis(client_with_fake_bridge, monkeypatch):
    # client_with_fake_bridge, not client: _run_pipeline_background calls
    # get_bridge() itself (to pass into run_pipeline — even though the
    # mocked run_pipeline below ignores it), so a real V1 bridge would
    # still be constructed and would need a real PRIVATE_KEY. Local dev
    # has one in .env; CI correctly doesn't fabricate a real Monad
    # testnet deployer key as a secret, so without this fixture
    # get_bridge() raises "PRIVATE_KEY not set in .env" — invisible
    # locally, a real CI failure (background task dies before publishing
    # a single event; the stream then times out after 120s).
    client = client_with_fake_bridge
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)

    user = seed_user_and_token()
    r = client.post("/run-agent", json={"task": "test task"}, headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    # The background task (asyncio.create_task) needs the event loop to
    # actually get a turn to run before we start reading — TestClient
    # shares the loop with the app here, so the GET below is what forces
    # that turn (Redis Streams doesn't care about ordering anyway; see
    # run_events.py's docstring on why a late reader still sees everything).
    with client.stream("GET", f"/stream/{run_id}") as response:
        assert response.status_code == 200
        events = []
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            # No early break on the first "run_complete": that's the
            # pipeline's OWN terminal event, not the stream's closing
            # marker (see main.py's stream_events) — the SECOND
            # "run_complete" only arrives after run_events.read_events()
            # itself finishes, which is the actual end of this generator.
            events.append(json.loads(line.removeprefix("data: ")))

    types_seen = [e.get("type") for e in events]
    assert "run_started" in types_seen
    # the real pipeline step event (no "type" key — see isStepEvent's
    # truthy-agentId-and-txHash contract in frontend/lib/types.ts)
    assert any(e.get("agentId") == "researcher" for e in events)
    # the pipeline's OWN run_complete, then the stream's synthetic
    # closing run_complete (see main.py's stream_events)
    assert types_seen.count("run_complete") == 2


def test_run_that_errors_internally_is_persisted_as_failed_not_stuck_running(
    client_with_fake_bridge, monkeypatch
):
    """Regression test for a real bug: agents/pipeline.py's run_pipeline()
    catches its OWN internal exceptions and yields a normal
    {"type": "error", ...} event rather than raising — so
    _run_pipeline_background's `async for` loop completes WITHOUT an
    exception. An earlier version of that function only called
    db.complete_run() on an explicit "run_complete" event and otherwise
    assumed success, calling neither db.complete_run() nor db.fail_run()
    for this case — the run's DB row stayed at status='running' forever,
    GET /runs/{run_id} 404'd "not yet complete" indefinitely, and the
    pipeline_runs_total metric recorded it as "completed" despite having
    failed. Caught for real via the Python SDK's integration tests
    polling GET /runs/{run_id} right after a genuine Groq rate-limit
    failure (sdk/python/tests/test_client.py).

    client_with_fake_bridge, not client — see the sibling test above for
    why: get_bridge() runs regardless of which run_pipeline is mocked in,
    and needs a real PRIVATE_KEY (present locally, correctly absent in
    CI) unless faked out."""
    client = client_with_fake_bridge
    import main

    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline_that_errors_internally)

    user = seed_user_and_token()
    r = client.post("/run-agent", json={"task": "test task"}, headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    with client.stream("GET", f"/stream/{run_id}") as response:
        for _ in response.iter_lines():
            pass  # drain to the stream's natural end, same reasoning as the SDK's stream()

    r = client.get(f"/runs/{run_id}", headers=_auth_headers(user["token"]))
    assert r.status_code == 200, f"run stayed unqueryable — expected the fix to persist a terminal status: {r.text}"
    # get_run() unwraps to the stored `result` once one exists (see
    # main.py's get_run — `return run["result"] if ... else run`);
    # db.fail_run() always sets result={"message": ...}, same shape a
    # caught main.py-level exception already produced before this fix —
    # what's new here is that this path is reached AT ALL for a pipeline-
    # internal error, not that the shape changes once it is.
    assert r.json()["message"] == "simulated internal pipeline failure"


def test_stream_of_unknown_run_id_eventually_times_out(client, monkeypatch):
    import main
    import run_events as run_events_module

    # Patch the timeout to something the test can actually wait for.
    original_read_events = run_events_module.read_events

    async def _short_timeout_read_events(run_id, timeout_seconds=120):
        async for evt in original_read_events(run_id, timeout_seconds=1):
            yield evt

    monkeypatch.setattr(main.run_events, "read_events", _short_timeout_read_events)

    with client.stream("GET", "/stream/run_that_was_never_created") as response:
        assert response.status_code == 200
        lines = [json.loads(l.removeprefix("data: ")) for l in response.iter_lines() if l.startswith("data: ")]

    assert len(lines) == 1
    assert lines[0]["type"] == "error"
    assert "timeout" in lines[0]["message"]


def test_read_events_maps_redis_client_side_timeout_to_builtin_timeout_error():
    """Regression test for a real bug: redis.exceptions.TimeoutError (the
    redis-py client's own socket read timing out — distinct from Redis's
    BLOCK argument naturally expiring with an empty response, which is
    what test_stream_of_unknown_run_id_eventually_times_out exercises) is
    NOT a subclass of the builtin TimeoutError (confirmed via its
    __mro__). Before this fix, run_events.read_events() let it propagate
    as-is, so main.py's `except TimeoutError:` in stream_events never
    caught it — the SSE stream just died mid-run with no error event ever
    reaching the client. Caught for real via the Python SDK's integration
    tests hammering GET /stream/{run_id} with many concurrent long-poll
    connections against a real Redis instance under load (see
    sdk/python/tests/test_client.py) — reproduced deterministically here
    without needing to induce that same load."""
    import asyncio

    import run_events as run_events_module

    class _FakeRedisThatTimesOut:
        async def xread(self, *args, **kwargs):
            raise redis.exceptions.TimeoutError("Timeout reading from redis:6379")

    async def _consume():
        async for _ in run_events_module.read_events("run_doesnt_matter", timeout_seconds=5):
            pass

    original_get_redis = run_events_module.get_redis
    run_events_module.get_redis = lambda: _FakeRedisThatTimesOut()
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(_consume())
    finally:
        run_events_module.get_redis = original_get_redis
