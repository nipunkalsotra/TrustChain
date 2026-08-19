"""tests/test_row_level_security.py — proves Postgres RLS actually
enforces tenant isolation at the DATABASE level, independent of any
Python-side project_id filter.

The rest of the test suite connects as the `trustchain` superuser
(DATABASE_URL in .env/CI), which — per Postgres semantics — bypasses RLS
unconditionally no matter what policies exist. That's intentional (see
db/engine.py's module docstring): RLS is a second, DB-enforced layer
under the `trustchain_api` role specifically, so it needs its own
dedicated connection to that role to actually exercise it.

Requires the RLS migration to have been applied
(`alembic upgrade head` — see alembic/versions/
9f3a1c7d5e2b_row_level_security_for_tenant_tables.py) against a REAL
Postgres; skipped otherwise rather than failing everyone who hasn't
migrated yet.
"""

import asyncio

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db
from config import get_settings
from db.engine import _set_rls_session_context, current_org_id, current_project_id, rls_bypass


def _api_role_database_url() -> str:
    """Same host/port/db as the superuser DATABASE_URL under test, but
    the restricted `trustchain_api` role the migration created."""
    base = get_settings().database_url
    # postgresql+asyncpg://trustchain:trustchain@host:port/db -> swap credentials only
    _, rest = base.split("://", 1)
    _creds, host_and_db = rest.split("@", 1)
    return f"postgresql+asyncpg://trustchain_api:trustchain_api_dev_password@{host_and_db}"


def _api_role_reachable() -> bool:
    async def _check():
        engine = create_async_engine(_api_role_database_url(), poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(_check())


pytestmark = pytest.mark.skipif(
    not _api_role_reachable(),
    reason="trustchain_api role not reachable — run `alembic upgrade head` against a real Postgres first",
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def api_role_session_factory():
    """A dedicated engine/sessionmaker for the restricted role, with the
    SAME RLS-context event listener db/engine.py registers on the app's
    real engine — so this test exercises the exact mechanism production
    uses, just against its own throwaway connection."""
    engine = create_async_engine(_api_role_database_url(), poolclass=NullPool)
    event.listen(engine.sync_engine, "begin", _set_rls_session_context)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    run(engine.dispose())


def _seed_two_projects():
    """Seeded via the normal (superuser) db module — real rows, real FKs,
    not fixtures standing in for them."""
    alice = run(db.create_user("rls_alice@example.com", "Alice", "hunter22", 1000))
    bob = run(db.create_user("rls_bob@example.com", "Bob", "hunter33", 1000))
    run(db.create_run("rls_run_alice", alice["projectId"], "alice task", "rls_alice@example.com", 2000))
    run(db.create_run("rls_run_bob", bob["projectId"], "bob task", "rls_bob@example.com", 2000))
    return alice, bob


async def _fetch_visible_run_ids(session_factory) -> list[str]:
    async with session_factory() as session:
        rows = (await session.execute(text("SELECT run_id FROM runs ORDER BY run_id"))).all()
        return [r[0] for r in rows]


def test_no_context_set_sees_zero_rows(api_role_session_factory):
    """Fail-closed: querying with no app.current_project_id GUC at all
    (e.g. a bug that forgets to resolve a Principal first) must return
    nothing, not everything."""
    _seed_two_projects()
    token = current_project_id.set(None)
    try:
        visible = run(_fetch_visible_run_ids(api_role_session_factory))
    finally:
        current_project_id.reset(token)
    assert visible == []


def test_project_context_scopes_visible_runs(api_role_session_factory):
    alice, bob = _seed_two_projects()

    token = current_project_id.set(alice["projectId"])
    try:
        visible_to_alice = run(_fetch_visible_run_ids(api_role_session_factory))
    finally:
        current_project_id.reset(token)
    assert visible_to_alice == ["rls_run_alice"]

    token = current_project_id.set(bob["projectId"])
    try:
        visible_to_bob = run(_fetch_visible_run_ids(api_role_session_factory))
    finally:
        current_project_id.reset(token)
    assert visible_to_bob == ["rls_run_bob"]


def test_cross_tenant_update_affects_zero_rows(api_role_session_factory):
    """The stronger claim: RLS doesn't just hide rows from SELECT, it
    makes them invisible to UPDATE/DELETE too — a bug that skips a
    project_id WHERE clause on a write can't touch another tenant's row
    even with a wide-open UPDATE ... SET ... (no WHERE at all)."""
    alice, bob = _seed_two_projects()

    async def _try_wipe_all_tasks():
        async with api_role_session_factory() as session:
            result = await session.execute(text("UPDATE runs SET task = 'HACKED'"))
            await session.commit()
            return result.rowcount

    token = current_project_id.set(alice["projectId"])
    try:
        rowcount = run(_try_wipe_all_tasks())
    finally:
        current_project_id.reset(token)

    assert rowcount == 1  # only alice's own row, despite no WHERE clause
    bob_run = run(db.get_run("rls_run_bob", bob["projectId"]))
    assert bob_run["task"] == "bob task"  # untouched


def test_steps_are_scoped_via_the_parent_run(api_role_session_factory):
    alice, bob = _seed_two_projects()

    async def _insert_step(run_id: str) -> None:
        async with db.engine.get_sessionmaker()() as session:
            await session.execute(
                text(
                    "INSERT INTO steps (run_id, agent_id, step_index, action, input_hash, output_hash, "
                    "leaf_hash, \"timestamp\", created_at) VALUES "
                    "(:run_id, 'a', 0, 'act', :h1, :h2, :leaf, 3000, 3000)"
                ),
                {"run_id": run_id, "h1": f"0x{'1'*64}", "h2": f"0x{'2'*64}", "leaf": f"0x{run_id[-8:].ljust(64, '0')}"},
            )
            await session.commit()

    run(_insert_step("rls_run_alice"))
    run(_insert_step("rls_run_bob"))

    async def _visible_steps():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT run_id FROM steps ORDER BY run_id"))).all()
            return [r[0] for r in rows]

    token = current_project_id.set(alice["projectId"])
    try:
        visible = run(_visible_steps())
    finally:
        current_project_id.reset(token)
    assert visible == ["rls_run_alice"]


def test_steps_history_is_scoped_by_its_own_denormalized_project_id(api_role_session_factory):
    """Phase 4 §3 step 13 — steps_history postdates this file's original
    coverage (added by migration b9a8a1970b3c, well after 9f3a1c7d5e2b's
    original RLS pass), so it's never been exercised here even though the
    migration that created it DID add real RLS for it (ENABLE + FORCE
    ROW LEVEL SECURITY + a tenant_isolation policy, same file). This
    confirms that policy actually holds — not just that it was written.

    Scoped by a column ON steps_history directly (project_id), not by a
    join through `steps` the way `steps` itself is scoped via `runs` —
    deliberately denormalized (see that migration's own comment) so this
    row's tenant scoping survives the referenced step later being
    DELETED entirely, exactly what integrity_watchdog's deletion-
    forensics path depends on (see test_integrity_detectors.py's
    test_deleting_the_only_step_in_a_batch_still_raises_an_alert)."""
    alice, bob = _seed_two_projects()

    async def _insert_history_row(project_id: int, step_id: int) -> None:
        async with db.engine.get_sessionmaker()() as session:
            await session.execute(
                text(
                    "INSERT INTO steps_history (step_id, project_id, changed_at, changed_columns) "
                    "VALUES (:step_id, :project_id, 4000, '[\"__deleted__\"]')"
                ),
                {"step_id": step_id, "project_id": project_id},
            )
            await session.commit()

    run(_insert_history_row(alice["projectId"], 90001))
    run(_insert_history_row(bob["projectId"], 90002))

    async def _visible_history_step_ids():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT step_id FROM steps_history ORDER BY step_id"))).all()
            return [r[0] for r in rows]

    token = current_project_id.set(alice["projectId"])
    try:
        visible = run(_visible_history_step_ids())
    finally:
        current_project_id.reset(token)
    assert visible == [90001]  # not bob's 90002, despite no application-level filter in this raw query


def test_rls_bypass_sees_every_tenant(api_role_session_factory):
    """Mirrors db/read_model.py's get_platform_stats — the one
    deliberate, explicit cross-tenant read."""
    _seed_two_projects()

    async def _with_bypass():
        with rls_bypass():
            async with api_role_session_factory() as session:
                rows = (await session.execute(text("SELECT run_id FROM runs ORDER BY run_id"))).all()
                return [r[0] for r in rows]

    assert run(_with_bypass()) == ["rls_run_alice", "rls_run_bob"]


def test_audit_events_are_scoped_by_org(api_role_session_factory):
    alice, bob = _seed_two_projects()

    async def _insert_event(org_id: int) -> None:
        async with db.engine.get_sessionmaker()() as session:
            await session.execute(
                text("INSERT INTO audit_events (org_id, action, created_at) VALUES (:org_id, 'test.action', 5000)"),
                {"org_id": org_id},
            )
            await session.commit()

    run(_insert_event(alice["orgId"]))
    run(_insert_event(bob["orgId"]))

    async def _visible_events():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT org_id FROM audit_events"))).all()
            return [r[0] for r in rows]

    token = current_org_id.set(alice["orgId"])
    try:
        visible = run(_visible_events())
    finally:
        current_org_id.reset(token)
    assert visible == [alice["orgId"]]


# ─────────────────────────────────────────────────────────────────────────
#  Phase 3 tables — migration d7e8f9a0b1c2. Same "seed via the real
#  superuser-role module functions, read back through the RLS-bound
#  api_role_session_factory" shape as every test above.
# ─────────────────────────────────────────────────────────────────────────

def test_alerts_are_scoped_by_org(api_role_session_factory):
    import db.alerts as alerts_db

    alice, bob = _seed_two_projects()
    run(alerts_db.raise_alert(
        org_id=alice["orgId"], alert_type="rls_test", severity="warning", title="t", summary="s",
        subject="rls:alice", evidence={}, detector="test",
    ))
    run(alerts_db.raise_alert(
        org_id=bob["orgId"], alert_type="rls_test", severity="warning", title="t", summary="s",
        subject="rls:bob", evidence={}, detector="test",
    ))

    async def _visible_subjects():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT subject FROM alerts"))).all()
            return [r[0] for r in rows]

    token = current_org_id.set(alice["orgId"])
    try:
        visible = run(_visible_subjects())
    finally:
        current_org_id.reset(token)
    assert visible == ["rls:alice"]


def test_alert_deliveries_are_scoped_via_parent_alert(api_role_session_factory):
    """Same shape as steps -> runs above: alert_deliveries has no org_id
    of its own, scoped via a join to alerts (migration d7e8f9a0b1c2)."""
    import db.alerts as alerts_db

    alice, bob = _seed_two_projects()
    run(alerts_db.raise_alert(
        org_id=alice["orgId"], alert_type="rls_test_delivery", severity="critical", title="t", summary="s",
        subject="rls:alice:delivery", evidence={}, detector="test",
    ))
    run(alerts_db.raise_alert(
        org_id=bob["orgId"], alert_type="rls_test_delivery", severity="critical", title="t", summary="s",
        subject="rls:bob:delivery", evidence={}, detector="test",
    ))

    async def _visible_recipients():
        async with api_role_session_factory() as session:
            rows = (await session.execute(
                text(
                    "SELECT ad.recipient FROM alert_deliveries ad "
                    "JOIN alerts a ON a.id = ad.alert_id WHERE a.alert_type = 'rls_test_delivery'"
                )
            )).all()
            return [r[0] for r in rows]

    # No recipients were queued (no owners/admins in these throwaway
    # orgs beyond the creating user, who has no NotificationPreference
    # row — defaults to opted-in, so the creator IS the recipient here).
    token = current_org_id.set(alice["orgId"])
    try:
        visible = run(_visible_recipients())
    finally:
        current_org_id.reset(token)
    assert all(r == "rls_alice@example.com" for r in visible)


def test_invitations_are_scoped_by_org(api_role_session_factory):
    import db.invitations as invitations_db

    alice, bob = _seed_two_projects()
    run(invitations_db.create_invitation(alice["orgId"], "invitee-alice@example.com", "member", alice["userId"], 9000, 604800))
    run(invitations_db.create_invitation(bob["orgId"], "invitee-bob@example.com", "member", bob["userId"], 9000, 604800))

    async def _visible_emails():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT email FROM invitations"))).all()
            return [r[0] for r in rows]

    token = current_org_id.set(alice["orgId"])
    try:
        visible = run(_visible_emails())
    finally:
        current_org_id.reset(token)
    assert visible == ["invitee-alice@example.com"]


def test_notification_preferences_are_scoped_by_org(api_role_session_factory):
    import db.alerts as alerts_db

    alice, bob = _seed_two_projects()
    run(alerts_db.set_notification_preferences(alice["userId"], alice["orgId"], True, True, False, False, 9000))
    run(alerts_db.set_notification_preferences(bob["userId"], bob["orgId"], True, True, False, False, 9000))

    async def _visible_user_ids():
        async with api_role_session_factory() as session:
            rows = (await session.execute(text("SELECT user_id FROM notification_preferences"))).all()
            return [r[0] for r in rows]

    token = current_org_id.set(alice["orgId"])
    try:
        visible = run(_visible_user_ids())
    finally:
        current_org_id.reset(token)
    assert visible == [alice["userId"]]


def test_watchdog_tables_are_not_reachable_by_api_role(api_role_session_factory):
    """watchdog_cursor/batch_verifications are deliberately NOT tenant
    data (no org_id/project_id column to key an RLS policy on at all) —
    migration d7e8f9a0b1c2 explicitly REVOKEs trustchain_api's access
    rather than leaving it implicitly reachable via the blanket
    default-privilege grant every other new table gets. This should fail
    with a permission error, not just return zero rows — those are two
    different guarantees (RLS hides ROWS; a REVOKE denies the query
    outright, closer to 'this table doesn't exist' from api's perspective)."""
    from sqlalchemy.exc import DBAPIError

    async def _try_read():
        async with api_role_session_factory() as session:
            await session.execute(text("SELECT * FROM watchdog_cursor"))

    with pytest.raises(DBAPIError):
        run(_try_read())
