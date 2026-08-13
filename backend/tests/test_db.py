import asyncio

import db


def run(coro):
    """db.py's functions are async (asyncio.to_thread wrappers); pytest-asyncio
    isn't a dependency here, so just drive the loop directly per test."""
    return asyncio.run(coro)


def test_create_and_authenticate_user():
    run(db.create_user("a@b.com", "Alice", "hunter22", 1000))

    ok = run(db.authenticate_user("a@b.com", "hunter22"))
    assert ok == {"email": "a@b.com", "name": "Alice"}

    bad = run(db.authenticate_user("a@b.com", "wrongpassword"))
    assert bad is None


def test_duplicate_user_raises():
    run(db.create_user("a@b.com", "Alice", "hunter22", 1000))
    try:
        run(db.create_user("a@b.com", "Someone Else", "otherpassword", 1001))
        assert False, "expected ValueError for duplicate email"
    except ValueError:
        pass


def test_password_hash_is_not_plaintext():
    run(db.create_user("a@b.com", "Alice", "hunter22", 1000))
    conn = db._get_conn()
    stored = conn.execute("SELECT password_hash FROM users WHERE email = ?", ("a@b.com",)).fetchone()
    assert stored["password_hash"] != "hunter22"
    assert "$" in stored["password_hash"]


def test_run_lifecycle():
    run(db.create_run("run_1", "do the thing", "a@b.com", 2000))

    mid = run(db.get_run("run_1"))
    assert mid["status"] == "running"
    assert mid["task"] == "do the thing"

    run(db.complete_run("run_1", {"type": "run_complete", "score": 91}, 2100))
    done = run(db.get_run("run_1"))
    assert done["status"] == "complete"
    assert done["result"] == {"type": "run_complete", "score": 91}
    # task/userEmail survive the completion update (only status/result/completedAt change)
    assert done["task"] == "do the thing"
    assert done["userEmail"] == "a@b.com"


def test_failed_run_records_error_message():
    run(db.create_run("run_2", "another task", None, 2200))
    run(db.fail_run("run_2", "boom", 2250))

    result = run(db.get_run("run_2"))
    assert result["status"] == "error"
    assert result["result"] == {"message": "boom"}


def test_list_runs_orders_newest_first():
    run(db.create_run("run_1", "first", None, 1000))
    run(db.create_run("run_2", "second", None, 2000))
    run(db.create_run("run_3", "third", None, 3000))

    runs = run(db.list_runs(limit=10))
    assert [r["runId"] for r in runs] == ["run_3", "run_2", "run_1"]


def test_get_run_returns_none_for_unknown_id():
    assert run(db.get_run("does-not-exist")) is None
