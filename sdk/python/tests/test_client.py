"""tests/test_client.py — integration tests for trustchain_sdk against a
REAL running API (docker compose up postgres redis anvil api anchor-worker
indexer mcp-search mcp-blockchain, with V2 contracts deployed) — no
mocking of httpx or the API. Skipped automatically if nothing answers at
BASE_URL, so this doesn't fail unrelated test runs in an environment that
doesn't have the stack up.

Run:
    docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
    pip install -e .[dev]
    pytest tests/ -v
"""

import uuid

import httpx
import pytest

from conftest import verified_signup
from trustchain_sdk import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    TrustChainClient,
    ValidationError,
)

BASE_URL = "http://localhost:8000"


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_is_up(), reason=f"no TrustChain API reachable at {BASE_URL}")


@pytest.fixture()
def api_key() -> str:
    """Signs up a FRESH human user + API key for every test function
    (deliberately function-scoped, not module-scoped) — every project
    gets its own POST /run-agent rate-limit bucket (backend/rate_limit.py,
    default capacity 5/min), so tests that each call run_agent() a few
    times must not share one project's bucket or they'll spuriously rate-
    limit each other depending on execution order. (First version of this
    fixture WAS module-scoped and hit exactly that: several unrelated
    tests failed with RateLimitError because five run_agent() calls
    across earlier tests had already exhausted the shared bucket.)"""
    email = f"sdk_test_{uuid.uuid4().hex}@example.com"
    jwt = verified_signup(BASE_URL, "SDK integration test", email, "sdk-test-password-123")

    created = httpx.post(
        f"{BASE_URL}/api-keys",
        json={"scopes": ["runs:write", "runs:read"], "environment": "test"},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=10.0,
    )
    assert created.status_code == 200, created.text
    return created.json()["raw_key"]


def test_run_agent_returns_run_id_and_stream_url(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        result = client.run_agent(f"sdk integration test task {uuid.uuid4().hex}")
        assert result["status"] == "started"
        assert result["run_id"]
        assert result["stream_url"] == f"/stream/{result['run_id']}"


def test_get_run_for_unknown_run_id_raises_not_found(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        with pytest.raises(NotFoundError):
            client.get_run("run_this_definitely_does_not_exist_00000000")


def test_list_runs_includes_a_run_just_created(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        started = client.run_agent(f"sdk integration test task {uuid.uuid4().hex}")
        listing = client.list_runs(limit=50)
        run_ids = [r["runId"] for r in listing["runs"]]
        assert started["run_id"] in run_ids


def test_list_agents_returns_empty_for_a_fresh_project():
    # A dedicated key (not the module `api_key` fixture, which only has
    # runs:write/runs:read) with agents:read specifically — read-model
    # only (backend/db/read_model.py::list_agents), no Anvil/on-chain
    # call needed, unlike register_agent/verify_agent.
    email = f"sdk_test_{uuid.uuid4().hex}@example.com"
    jwt = verified_signup(BASE_URL, "SDK integration test", email, "sdk-test-password-123")
    created = httpx.post(
        f"{BASE_URL}/api-keys",
        json={"scopes": ["agents:read"], "environment": "test"},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=10.0,
    )
    assert created.status_code == 200, created.text

    with TrustChainClient(created.json()["raw_key"], base_url=BASE_URL) as client:
        result = client.list_agents()
        assert result == {"agents": [], "total": 0}


def test_stream_yields_events_ending_in_a_terminal_event(api_key):
    # The stream's actual last event is a synthetic "run_complete"
    # WRAPPER main.py's stream_events always appends after the pipeline's
    # own events (see that function's docstring), sent only once the
    # run's terminal status is already committed to Postgres — this is
    # true whether the pipeline itself succeeded or errored (the wrapper
    # follows an "error" event too). Only a genuine stream TIMEOUT skips
    # the wrapper (main.py's `except TimeoutError` branch sends "error" as
    # the true final event with nothing after it) — not exercised here.
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        started = client.run_agent(f"sdk integration test task {uuid.uuid4().hex}")
        events = list(client.stream(started["run_id"], timeout=90))
        assert events, "expected at least one SSE event"
        assert events[-1]["type"] == "run_complete"
        assert all(e.get("runId") == started["run_id"] for e in events if "runId" in e)


def test_run_and_wait_blocks_until_terminal_event(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        final_event = client.run_and_wait(f"sdk integration test task {uuid.uuid4().hex}", timeout=90)
        assert final_event["type"] == "run_complete"


def test_get_run_after_run_and_wait_is_immediately_queryable(api_key):
    # Regression test: run_and_wait()/stream() must consume the SSE
    # stream to its natural end (not stop early on the pipeline's own
    # "error"/"run_complete" event) specifically so that by the time they
    # return, GET /runs/{run_id} is guaranteed queryable — no retry/
    # backoff here on purpose, since needing one would mean the race is
    # back. See stream()'s docstring for the race this was previously
    # vulnerable to (an earlier version returned as soon as it saw the
    # pipeline's own terminal-typed event, racing ahead of the server's
    # own db.complete_run/fail_run write).
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        started = client.run_agent(f"sdk integration test task {uuid.uuid4().hex}")
        for _ in client.stream(started["run_id"], timeout=90):
            pass

        # The whole point of this test: get_run() must succeed (not 404
        # "not yet complete") once the stream has fully drained. Its
        # response shape legitimately differs between a successful run
        # (db.complete_run stores the run_complete event verbatim —
        # {type, runId, report, score, ...}) and a failed one
        # (db.fail_run stores only {"message": ...}, see main.py's
        # get_run — "return run['result'] if ... else run"), so this
        # doesn't assert on shape, only that it's reachable at all.
        client.get_run(started["run_id"])


def test_trust_scores_after_a_completed_run(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        started = client.run_agent(f"sdk integration test task {uuid.uuid4().hex}")
        for _ in client.stream(started["run_id"], timeout=90):
            pass  # drain to the stream's natural end — see stream()'s docstring

        # get_run() returns the stored `result` unwrapped once terminal
        # (see main.py's get_run) — a successful run's result is the
        # run_complete event verbatim (has "score"); a failed run's is
        # just {"message": ...}. Check for "score" rather than a "status"
        # field, which this response shape never has.
        run = client.get_run(started["run_id"])
        if "score" not in run:
            pytest.skip(f"run did not complete successfully — nothing to check scores for: {run}")
        scores = client.trust_scores(started["run_id"])
        assert scores["runId"] == started["run_id"]
        assert isinstance(scores["scores"], (list, dict))


def test_leaderboard_and_audit_log_return_real_data_shapes(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        leaderboard = client.leaderboard()
        assert set(leaderboard.keys()) == {"agents", "totalRuns", "runsConsidered"}

        audit = client.audit_log()
        assert set(audit.keys()) == {"entries", "total"}
        assert audit["total"] == len(audit["entries"])


def test_invalid_api_key_raises_authentication_error():
    with TrustChainClient("tc_test_not_a_real_key_00000000000000000000", base_url=BASE_URL) as client:
        with pytest.raises(AuthenticationError) as exc_info:
            client.list_runs()
    # error_code (backend/errors.py's typed taxonomy) must reach the SDK
    # exception object, not just the raw response body — this is what a
    # caller actually branches on instead of parsing .detail strings.
    assert exc_info.value.error_code == "invalid_api_key"


def test_empty_task_raises_validation_error(api_key):
    # RunAgentRequest.task has Field(min_length=1) — an empty string is
    # now rejected at the Pydantic schema layer (422) before main.py's
    # handler body ever runs, not by its own `if not body.task.strip()`
    # check (400) — that check is still real, just now only reachable
    # for a WHITESPACE-only task ("   "), which passes min_length=1
    # (length 3) but still isn't a usable task.
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        with pytest.raises(ValidationError):
            client.run_agent("")


def test_whitespace_only_task_raises_bad_request_error(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        with pytest.raises(BadRequestError) as exc_info:
            client.run_agent("   ")
    assert exc_info.value.error_code == "task_empty"


def test_bursting_past_the_rate_limit_raises_rate_limit_error_with_retry_after(api_key):
    with TrustChainClient(api_key, base_url=BASE_URL) as client:
        seen_rate_limit_error = False
        for _ in range(20):
            try:
                client.run_agent(f"sdk integration test burst {uuid.uuid4().hex}")
            except RateLimitError as e:
                seen_rate_limit_error = True
                assert e.retry_after_seconds is not None
                break
        assert seen_rate_limit_error, "expected at least one RateLimitError from a 20-call burst"
