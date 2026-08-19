"""
tests/test_watchdog_sweep.py — the integrity watchdog's own machinery:
the rolling cursor's advance/wrap behavior, the sole-active-sweeper
advisory lock, and that a full cycle actually catches tampering via both
the hot and rolling tiers (Phase 3 §6.7/ADR-0015).
"""

import asyncio
import time

from sqlalchemy import select, text

import db
import observability
from agents.base import log_step
from db.engine import get_sessionmaker
from db.models import Alert
from integrity_watchdog.cursor import advance_cursor, get_cursor
from integrity_watchdog.lock import WatchdogLock
from integrity_watchdog.main import run_cycle
from tests.conftest import seed_project


def run(coro):
    return asyncio.run(coro)


# ── Advisory lock ────────────────────────────────────────────────────

def test_advisory_lock_prevents_a_second_active_sweeper():
    """Everything inside ONE run() call, not one call per step — the lock
    holds a real asyncpg connection tied to whichever event loop
    acquired it (see WatchdogLock's own docstring: advisory locks are
    session-scoped). asyncio.run() tears its loop down when it returns,
    so acquiring in one run() call and releasing in a LATER, separate
    run() call hands that connection to a since-closed loop — real
    production usage never does this (one process, one asyncio.run(main())
    for its entire lifetime), so this test drives the whole scenario
    through a single event loop to match."""
    async def _scenario():
        lock_a = WatchdogLock()
        lock_b = WatchdogLock()

        assert await lock_a.try_acquire() is True
        assert await lock_b.try_acquire() is False  # a second instance must not also become active

        await lock_a.release()

        assert await lock_b.try_acquire() is True
        await lock_b.release()

    run(_scenario())


# ── Rolling cursor ───────────────────────────────────────────────────

def test_cursor_advances_and_records_last_run_metadata():
    async def _advance():
        async with get_sessionmaker()() as session:
            before = await get_cursor(session, "test_detector_advance")
            assert before["lastId"] == 0
            await advance_cursor(session, "test_detector_advance", new_last_id=42, wrapped=False, duration_ms=123)
            after = await get_cursor(session, "test_detector_advance")
            return after

    after = run(_advance())
    assert after["lastId"] == 42
    assert after["wrappedAt"] is None
    assert after["lastDurationMs"] == 123


def test_cursor_wrap_resets_to_zero_and_records_wrapped_at():
    async def _wrap():
        async with get_sessionmaker()() as session:
            await advance_cursor(session, "test_detector_wrap", new_last_id=99, wrapped=False, duration_ms=10)
            await advance_cursor(session, "test_detector_wrap", new_last_id=0, wrapped=True, duration_ms=5)
            return await get_cursor(session, "test_detector_wrap")

    after = run(_wrap())
    assert after["lastId"] == 0
    assert after["wrappedAt"] is not None


# ── run_cycle — the whole loop, one pass ────────────────────────────

def _seed_run_with_step(run_id: str, project_id: int):
    run(db.create_run(run_id, project_id, "watchdog sweep test", None, int(time.time())))
    _, event = run(log_step(
        bridge=None, agent_id="support-bot", action="answer_query",
        input_text="hello", output_text="world", step_index=0, run_id=run_id,
    ))
    return event["stepId"]


async def _org_id_for_project(project_id: int) -> int:
    from db.models import Project
    async with get_sessionmaker()() as session:
        p = await session.get(Project, project_id)
        return p.org_id


def test_run_cycle_hot_tier_catches_a_step_tampered_moments_ago():
    """The hot tier scans everything created within the recent window on
    EVERY cycle — a step tampered with just now must be caught without
    waiting for the rolling cursor to reach it, which could otherwise
    take many cycles over a large backlog."""
    project_id = seed_project()
    step_id = _seed_run_with_step("watchdog_hot_run", project_id)

    async def _tamper():
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE steps SET output_hash = :fake WHERE id = :id"), {"fake": "0x" + "9" * 64, "id": step_id})
            await session.commit()

    run(_tamper())
    run(run_cycle())

    org_id = run(_org_id_for_project(project_id))

    async def _find():
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "step_row_tampered")
            )).scalar_one_or_none()

    assert run(_find()) is not None


def test_run_cycle_rolling_tier_respects_its_per_cycle_budget(monkeypatch):
    """Seeds more steps than one cycle's budget allows, and confirms the
    cursor doesn't jump past that budget in a single pass — the actual
    'cost stays flat as history grows' property (ADR-0015)."""
    from config import get_settings
    monkeypatch.setenv("WATCHDOG_ROLLING_STEPS_PER_CYCLE", "3")
    monkeypatch.setenv("WATCHDOG_HOT_WINDOW_SECONDS", "0")  # exclude these from the hot tier so only the rolling tier's budget is under test
    get_settings.cache_clear()

    # watchdog_cursor is deliberately NOT tenant-scoped (one global
    # rolling sweep across every project, by design — see its own model
    # docstring) — isolated_db's per-test truncation only covers tenant
    # tables, so it does NOT reset this between tests. Any other test in
    # this file that also calls run_cycle() (e.g. the hot-tier test
    # above) advances this SAME "step_rows_rolling" cursor as a side
    # effect. Reset it explicitly so this test's assertion holds
    # regardless of what ran before it — a real gap this test's own
    # first run caught (off-by-one against a stale cursor left over from
    # an earlier test).
    async def _reset_cursor():
        async with get_sessionmaker()() as session:
            await advance_cursor(session, "step_rows_rolling", new_last_id=0, wrapped=False, duration_ms=0)

    run(_reset_cursor())

    project_id = seed_project()
    step_ids = [_seed_run_with_step(f"watchdog_budget_run_{i}", project_id) for i in range(10)]

    run(run_cycle())

    async def _cursor():
        async with get_sessionmaker()() as session:
            return await get_cursor(session, "step_rows_rolling")

    cursor_after_one_cycle = run(_cursor())
    # Exactly 3 (the configured budget) steps were swept, not all 10 —
    # the cursor's lastId is the 3rd seeded step's id, not the 10th.
    assert cursor_after_one_cycle["lastId"] == step_ids[2]

    get_settings.cache_clear()


def test_run_cycle_liveness_detects_a_stalled_outbox():
    project_id = seed_project()
    run(db.create_run("watchdog_liveness_run", project_id, "task", None, int(time.time())))
    _, event = run(log_step(
        bridge=None, agent_id="support-bot", action="answer_query", input_text="x", output_text="y",
        step_index=0, run_id="watchdog_liveness_run",
    ))

    async def _backdate_outbox():
        async with get_sessionmaker()() as session:
            await session.execute(
                text("UPDATE anchor_outbox SET created_at = :old WHERE step_id = :sid"),
                {"old": int(time.time()) - 100_000, "sid": event["stepId"]},
            )
            await session.commit()

    run(_backdate_outbox())
    run(run_cycle())

    org_id = run(_org_id_for_project(project_id))

    async def _find():
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(Alert).where(Alert.org_id == org_id, Alert.alert_type == "anchoring_stalled")
            )).scalar_one_or_none()

    assert run(_find()) is not None


def test_run_cycle_sets_the_open_alerts_gauge_by_severity():
    """observability.OPEN_ALERTS is a platform-wide Gauge (no tenant
    label — see its own docstring) that Grafana's 'Open alerts by
    severity' panel reads directly. A real gap found while manually
    checking that dashboard against a live stack: the Gauge was defined
    in observability.py but nothing ever called .set() on it, so the
    panel silently showed 'No data' forever regardless of how many real
    alerts existed. Fixed by recomputing it fresh every cycle in
    run_cycle() itself (see the comment there for why recompute-not-
    increment). This test would have caught that gap: it fails on the
    pre-fix code because the Gauge's child never gets created at all."""
    project_id = seed_project()
    step_id = _seed_run_with_step("watchdog_open_alerts_run", project_id)

    async def _tamper():
        async with get_sessionmaker()() as session:
            await session.execute(text("UPDATE steps SET output_hash = :fake WHERE id = :id"), {"fake": "0x" + "8" * 64, "id": step_id})
            await session.commit()

    run(_tamper())
    run(run_cycle())

    # isolated_db truncates the (tenant-scoped) alerts table before/after
    # every test, so this is exactly the one alert this test itself
    # raised — not a >= against unrelated leftover state.
    assert observability.OPEN_ALERTS.labels(severity="critical")._value.get() == 1
    assert observability.OPEN_ALERTS.labels(severity="warning")._value.get() == 0
    assert observability.OPEN_ALERTS.labels(severity="info")._value.get() == 0
