"""
tests/test_verify_content.py — POST /integrity/verify-content: turning an
opaque hash in an alert's evidence into an actual yes/no answer about
content, without TrustChain ever storing that content itself. The owner
supplies a candidate they already have from their own systems; this
confirms or denies it against the step's current hash and — via
steps_history — what the hash was before any tampering.
"""

import asyncio
import time

import db
from agents.base import log_step
from db.engine import get_sessionmaker
from tests.conftest import seed_user_and_token


def run(coro):
    return asyncio.run(coro)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_step(project_id: int, output_text: str = "the refund is $50") -> int:
    run_id = f"verify_content_run_{int(time.time() * 1000)}"
    run(db.create_run(run_id, project_id, "verify-content test", None, int(time.time())))
    _, event = run(log_step(
        bridge=None, agent_id="support-bot", action="answer_query",
        input_text="what is my refund", output_text=output_text, step_index=0, run_id=run_id,
    ))
    return event["stepId"]


def test_correct_candidate_matches_current_hash_with_no_history(client):
    user = seed_user_and_token(email="verify_content_owner1@example.com")
    step_id = _seed_step(user["projectId"], output_text="the refund is $50")

    r = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "output", "candidateText": "the refund is $50"},
        headers=_auth(user["token"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matchesCurrent"] is True
    assert body["matchesOriginal"] is None  # no steps_history row — nothing's ever been changed


def test_wrong_candidate_matches_neither(client):
    user = seed_user_and_token(email="verify_content_owner2@example.com")
    step_id = _seed_step(user["projectId"], output_text="the refund is $50")

    r = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "output", "candidateText": "something else entirely"},
        headers=_auth(user["token"]),
    )
    assert r.status_code == 200
    assert r.json()["matchesCurrent"] is False


def test_after_tampering_original_candidate_matches_history_not_current(client):
    """The actual scenario this endpoint exists for: an owner reads a
    step_row_tampered alert, suspects the original said 'the refund is
    $50' (e.g. because that's what THEIR OWN logs say), and wants to
    confirm it — without TrustChain ever having stored that text itself."""
    user = seed_user_and_token(email="verify_content_owner3@example.com")
    step_id = _seed_step(user["projectId"], output_text="the refund is $50")

    async def _tamper():
        from sqlalchemy import text
        async with get_sessionmaker()() as session:
            await session.execute(
                text("UPDATE steps SET output_hash = '0x' || repeat('f', 64) WHERE id = :id"), {"id": step_id}
            )
            await session.commit()

    run(_tamper())

    # The real original text — the owner's own copy, from outside TrustChain.
    r_original = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "output", "candidateText": "the refund is $50"},
        headers=_auth(user["token"]),
    )
    assert r_original.status_code == 200
    body = r_original.json()
    assert body["matchesCurrent"] is False  # the row was tampered — no longer matches
    assert body["matchesOriginal"] is True  # but it DID match what the hash was before the edit

    # A wrong guess at the original — must not falsely confirm it.
    r_wrong = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "output", "candidateText": "the refund is $5000"},
        headers=_auth(user["token"]),
    )
    assert r_wrong.json()["matchesOriginal"] is False


def test_cannot_verify_content_for_another_tenants_step(client):
    owner = seed_user_and_token(email="verify_content_owner4@example.com")
    step_id = _seed_step(owner["projectId"])

    intruder = seed_user_and_token(email="verify_content_intruder4@example.com")
    r = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "output", "candidateText": "the refund is $50"},
        headers=_auth(intruder["token"]),
    )
    assert r.status_code == 404  # same "looks like it doesn't exist" shape as verify_run, not a 403


def test_unknown_field_value_is_rejected(client):
    """field is a Literal["input", "output"] on the request model — an
    invalid value should be a standard 422 from Pydantic, not a 500 or a
    silently-accepted garbage value."""
    user = seed_user_and_token(email="verify_content_owner5@example.com")
    step_id = _seed_step(user["projectId"])

    r = client.post(
        "/integrity/verify-content",
        json={"stepId": step_id, "field": "not_a_real_field", "candidateText": "x"},
        headers=_auth(user["token"]),
    )
    assert r.status_code == 422
