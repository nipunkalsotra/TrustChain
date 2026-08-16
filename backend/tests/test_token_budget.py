"""
Tests for LLM token budget enforcement (plan O10) — two independent
layers, tested separately:

  1. Aggregate, per-org: POST /run-agent checks Organization.tokens_spent
     against Organization.token_budget BEFORE spawning a run at all (real
     HTTP + real Postgres, mirroring test_anchor_worker.py's gas-ceiling
     tests). The background pipeline itself is faked here (same
     established pattern as test_rate_limiting.py/test_tenant_log_
     correlation.py) — this suite is about the budget check and the
     post-completion spend recording, not about exercising a real Groq
     call, which agents/pipeline.py's own __main__ smoke test and the
     live stack already cover.

  2. Per-run: agents.base.track_token_usage is a pure function, tested
     directly against fake response objects carrying a real
     langchain usage_metadata shape.
"""

from sqlalchemy import text as _text

from tests.conftest import FakeBridge, seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _set_org_token_budget(org_id: int, token_budget) -> None:
    from db.engine import get_sessionmaker

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            _text("UPDATE organizations SET token_budget = :budget WHERE id = :org_id"),
            {"budget": token_budget, "org_id": org_id},
        )
        await session.commit()


async def _get_org_tokens_spent(org_id: int) -> int:
    from db.engine import get_sessionmaker

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = await session.execute(
            _text("SELECT tokens_spent FROM organizations WHERE id = :org_id"), {"org_id": org_id},
        )
        return row.scalar_one()


def run(coro):
    import asyncio

    return asyncio.run(coro)


def _poll_until(read, is_done, timeout_seconds: float = 3.0, interval_seconds: float = 0.05):
    """Polls `read()` until `is_done(value)` or timeout — see the one
    caller below for why: the background pipeline task isn't awaited by
    the request handler, so a value it eventually writes may not be there
    the instant the HTTP response comes back."""
    import time

    deadline = time.monotonic() + timeout_seconds
    value = read()
    while not is_done(value) and time.monotonic() < deadline:
        time.sleep(interval_seconds)
        value = read()
    return value


async def _fake_run_pipeline_with_tokens(tokens_used: int):
    async def _pipeline(task, run_id=None, bridge=None):
        yield {"type": "run_started", "runId": run_id, "task": task}
        yield {
            "type": "run_complete", "runId": run_id, "report": "done", "score": 91,
            "txCount": 0, "txHashes": [], "tokensUsed": tokens_used,
        }

    return _pipeline


# ── Aggregate (org-level) budget — POST /run-agent pre-check ───────────────

def test_run_agent_rejects_when_org_token_budget_already_breached(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "run_pipeline", run(_fake_run_pipeline_with_tokens(0)))

    user = seed_user_and_token("token-budget-breached@example.com", "TokenBudgetBreached")
    run(_set_org_token_budget(user["orgId"], 0))  # already at/over a zero ceiling

    r = client.post("/run-agent", json={"task": "should be rejected"}, headers=_auth_headers(user["token"]))

    assert r.status_code == 429, r.text
    assert r.json()["error_code"] == "token_budget_exceeded"


def test_run_agent_succeeds_and_records_real_token_spend_when_under_budget(client, monkeypatch):
    """Real end-to-end (through the HTTP layer and real Postgres) for the
    part that doesn't need a real Groq call: the aggregate spend counter
    only exists to be updated with whatever run_pipeline's run_complete
    event reports, so a fake pipeline reporting a known tokensUsed value
    is exactly as real a test of record_token_spend's wiring as a genuine
    LLM call would be — the number's origin (real usage_metadata vs this
    fake) is agents/base.py's track_token_usage's concern, covered
    separately below."""
    import main

    monkeypatch.setattr(main, "get_bridge", lambda: FakeBridge())
    monkeypatch.setattr(main, "run_pipeline", run(_fake_run_pipeline_with_tokens(1234)))

    user = seed_user_and_token("token-budget-under@example.com", "TokenBudgetUnder")
    run(_set_org_token_budget(user["orgId"], 1_000_000))  # generous, not breached

    before = run(_get_org_tokens_spent(user["orgId"]))
    assert before == 0

    import observability
    org_id = user["orgId"]
    metric_before = observability.LLM_TOKENS_USED_TOTAL.labels(org_id=str(org_id))._value.get()

    r = client.post("/run-agent", json={"task": "should succeed"}, headers=_auth_headers(user["token"]))
    assert r.status_code == 200, r.text

    # The spend recording happens inside _run_pipeline_background, an
    # asyncio.create_task() the request handler fires and does NOT await
    # (see main.py's F14 shutdown-drain docstring) — TestClient's response
    # returning is no guarantee that task has run yet, so poll briefly
    # rather than assert immediately.
    after = _poll_until(lambda: run(_get_org_tokens_spent(user["orgId"])), lambda spent: spent != 0)
    # record_token_spend committing is the LAST database write
    # _run_pipeline_background makes, but the task itself (metrics,
    # tracer span exit, its DB session's own connection-close) can still
    # be unwinding for a moment after that commit is visible — a brief
    # grace period here avoids a real, observed race against the next
    # test's isolated_db fixture TRUNCATE-ing while that connection is
    # still being returned to the pool (Postgres deadlock, not a bug in
    # the code under test).
    import time as _time
    _time.sleep(0.2)
    assert after == before + 1234

    metric_after = observability.LLM_TOKENS_USED_TOTAL.labels(org_id=str(org_id))._value.get()
    assert metric_after == metric_before + 1234


def test_gas_spend_endpoint_surfaces_org_token_budget_status(client):
    user = seed_user_and_token("token-budget-surface@example.com", "TokenBudgetSurface")
    run(_set_org_token_budget(user["orgId"], 5000))

    r = client.get("/gas-spend", headers=_auth_headers(user["token"]))
    assert r.status_code == 200, r.text
    assert r.json()["orgTokenBudget"] == {"tokenBudget": 5000, "tokensSpent": 0, "breached": False}


# ── Per-run cap — agents.base.track_token_usage (pure function) ────────────

class _FakeUsageResponse:
    def __init__(self, total_tokens: int):
        self.usage_metadata = {"input_tokens": 0, "output_tokens": total_tokens, "total_tokens": total_tokens}


def test_track_token_usage_accumulates_across_calls():
    from agents.base import track_token_usage

    total = track_token_usage(0, _FakeUsageResponse(100), "run_1")
    assert total == 100
    total = track_token_usage(total, _FakeUsageResponse(50), "run_1")
    assert total == 150


def test_track_token_usage_raises_once_the_per_run_ceiling_is_crossed(monkeypatch):
    import config
    from agents.base import TokenBudgetExceeded, track_token_usage

    monkeypatch.setattr(config.get_settings(), "llm_token_budget_per_run", 100)

    total = track_token_usage(0, _FakeUsageResponse(80), "run_2")
    assert total == 80

    try:
        track_token_usage(total, _FakeUsageResponse(30), "run_2")
        assert False, "expected TokenBudgetExceeded"
    except TokenBudgetExceeded as e:
        assert "run_2" in str(e)


def test_track_token_usage_handles_a_response_with_no_usage_metadata():
    from agents.base import track_token_usage

    class _NoUsage:
        pass

    total = track_token_usage(10, _NoUsage(), "run_3")
    assert total == 10
