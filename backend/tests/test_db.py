import asyncio

import db
from tests.conftest import seed_project


def run(coro):
    """db.py's functions are async (asyncio.to_thread wrappers); pytest-asyncio
    isn't a dependency here, so just drive the loop directly per test."""
    return asyncio.run(coro)


def test_create_and_authenticate_user():
    created = run(db.create_user("a@b.com", "Alice", "hunter22", 1000))

    ok = run(db.authenticate_user("a@b.com", "hunter22"))
    assert ok["email"] == "a@b.com"
    assert ok["name"] == "Alice"
    # Every user gets a real, auto-provisioned project (Phase 2.3
    # multi-tenancy) — same one create_user itself returned.
    assert ok["projectId"] == created["projectId"]
    assert ok["orgId"] == created["orgId"]

    bad = run(db.authenticate_user("a@b.com", "wrongpassword"))
    assert bad is None


def test_signup_provisions_a_distinct_project_per_user():
    alice = run(db.create_user("alice@example.com", "Alice", "hunter22", 1000))
    bob = run(db.create_user("bob@example.com", "Bob", "hunter33", 1000))
    assert alice["projectId"] != bob["projectId"]
    assert alice["orgId"] != bob["orgId"]


def test_duplicate_user_raises():
    run(db.create_user("a@b.com", "Alice", "hunter22", 1000))
    try:
        run(db.create_user("a@b.com", "Someone Else", "otherpassword", 1001))
        assert False, "expected ValueError for duplicate email"
    except ValueError:
        pass


def test_password_hash_is_not_plaintext():
    run(db.create_user("a@b.com", "Alice", "hunter22", 1000))

    async def _fetch_hash():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import User

        async with get_sessionmaker()() as session:
            result = await session.execute(select(User).where(User.email == "a@b.com"))
            return result.scalar_one().password_hash

    stored_hash = run(_fetch_hash())
    assert stored_hash != "hunter22"
    assert "$" in stored_hash


def test_new_users_get_argon2id_hashes():
    run(db.create_user("argon@b.com", "Argon", "hunter22", 1000))

    async def _fetch_hash():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import User

        async with get_sessionmaker()() as session:
            result = await session.execute(select(User).where(User.email == "argon@b.com"))
            return result.scalar_one().password_hash

    assert run(_fetch_hash()).startswith("$argon2id$")


def test_legacy_pbkdf2_hash_still_verifies_and_is_upgraded_in_place():
    """Simulates a pre-Argon2id user row (Phase 1's PBKDF2-HMAC-SHA256
    format) — must still authenticate, then be transparently rehashed to
    Argon2id on that same successful login so no forced-reset migration
    is ever needed."""

    async def _seed_legacy_user():
        from db.engine import get_sessionmaker
        from db import tenancy
        from db.models import User

        async with get_sessionmaker()() as session:
            user = User(email="legacy@b.com", name="Legacy", password_hash=db._hash_password_pbkdf2("hunter22"), created_at=1000)
            session.add(user)
            await session.flush()
            await tenancy.provision_personal_org(session, user.id, "Legacy", 1000)
            await session.commit()

    async def _fetch_hash():
        from sqlalchemy import select
        from db.engine import get_sessionmaker
        from db.models import User

        async with get_sessionmaker()() as session:
            result = await session.execute(select(User).where(User.email == "legacy@b.com"))
            return result.scalar_one().password_hash

    run(_seed_legacy_user())
    hash_before = run(_fetch_hash())
    assert not hash_before.startswith("$argon2")

    ok = run(db.authenticate_user("legacy@b.com", "hunter22"))
    assert ok["email"] == "legacy@b.com"

    hash_after = run(_fetch_hash())
    assert hash_after.startswith("$argon2id$")
    assert hash_after != hash_before

    # The upgraded hash must itself authenticate correctly going forward,
    # and a wrong password must still be rejected.
    assert run(db.authenticate_user("legacy@b.com", "hunter22")) is not None
    assert run(db.authenticate_user("legacy@b.com", "wrongpassword")) is None


def test_run_lifecycle():
    user = run(db.create_user("a@b.com", "Alice", "hunter22", 1000))
    project_id = user["projectId"]

    run(db.create_run("run_1", project_id, "do the thing", "a@b.com", 2000))

    mid = run(db.get_run("run_1", project_id))
    assert mid["status"] == "running"
    assert mid["task"] == "do the thing"

    run(db.complete_run("run_1", {"type": "run_complete", "score": 91}, 2100))
    done = run(db.get_run("run_1", project_id))
    assert done["status"] == "complete"
    assert done["result"] == {"type": "run_complete", "score": 91}
    # task/userEmail survive the completion update (only status/result/completedAt change)
    assert done["task"] == "do the thing"
    assert done["userEmail"] == "a@b.com"


def test_get_run_returns_none_for_another_project():
    """Invariant I7: a run_id that exists but belongs to a different
    project must be indistinguishable from one that never existed."""
    alice = run(db.create_user("alice@example.com", "Alice", "hunter22", 1000))
    bob = run(db.create_user("bob@example.com", "Bob", "hunter33", 1000))

    run(db.create_run("run_alice", alice["projectId"], "alice task", "alice@example.com", 2000))

    assert run(db.get_run("run_alice", bob["projectId"])) is None
    assert run(db.get_run("run_alice", alice["projectId"])) is not None


def test_failed_run_records_error_message():
    project_id = seed_project()
    run(db.create_run("run_2", project_id, "another task", None, 2200))
    run(db.fail_run("run_2", "boom", 2250))

    result = run(db.get_run("run_2", project_id))
    assert result["status"] == "error"
    assert result["result"] == {"message": "boom"}


def test_list_runs_orders_newest_first():
    project_id = seed_project()
    run(db.create_run("run_1", project_id, "first", None, 1000))
    run(db.create_run("run_2", project_id, "second", None, 2000))
    run(db.create_run("run_3", project_id, "third", None, 3000))

    runs = run(db.list_runs(project_id, limit=10))
    assert [r["runId"] for r in runs] == ["run_3", "run_2", "run_1"]


def test_list_runs_does_not_include_another_projects_runs():
    project_a = seed_project("project a")
    project_b = seed_project("project b")
    run(db.create_run("run_a", project_a, "task a", None, 1000))
    run(db.create_run("run_b", project_b, "task b", None, 1000))

    runs_a = run(db.list_runs(project_a, limit=10))
    assert [r["runId"] for r in runs_a] == ["run_a"]


def test_get_run_returns_none_for_unknown_id():
    project_id = seed_project()
    assert run(db.get_run("does-not-exist", project_id)) is None
