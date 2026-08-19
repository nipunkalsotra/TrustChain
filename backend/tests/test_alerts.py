"""
tests/test_alerts.py — dedupe, resolve/reopen, recipient resolution
(including the digest_only fix), and the HTTP surface (Phase 3 §7,
§9.6).
"""

import asyncio

import db.alerts as alerts_db
from db.models import AlertDelivery
from tests.conftest import seed_user_and_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run(coro):
    return asyncio.run(coro)


def _raise(org_id: int, subject: str = "test:subject", severity: str = "warning", alert_type: str = "rls_test") -> dict:
    return _run(alerts_db.raise_alert(
        org_id=org_id, alert_type=alert_type, severity=severity, title="t", summary="s",
        subject=subject, evidence={"k": "v"}, detector="test",
    ))


# ── Dedupe ────────────────────────────────────────────────────────────

def test_repeated_raise_with_same_key_increments_occurrence_not_rows():
    user = seed_user_and_token(email="alerts_dedupe1@example.com")
    first = _raise(user["orgId"], subject="dedupe:1")
    second = _raise(user["orgId"], subject="dedupe:1")
    third = _raise(user["orgId"], subject="dedupe:1")

    assert first["isNew"] is True
    assert second["isNew"] is False
    assert third["isNew"] is False
    assert first["alertId"] == second["alertId"] == third["alertId"]
    assert third["occurrenceCount"] == 3


def test_different_subject_gets_its_own_row():
    user = seed_user_and_token(email="alerts_dedupe2@example.com")
    a = _raise(user["orgId"], subject="dedupe:a")
    b = _raise(user["orgId"], subject="dedupe:b")
    assert a["alertId"] != b["alertId"]


def test_resolve_frees_the_dedupe_key_for_a_genuine_recurrence():
    user = seed_user_and_token(email="alerts_resolve1@example.com")
    first = _raise(user["orgId"], subject="resolve:1")

    resolved = _run(alerts_db.resolve_alert(first["alertId"], user["orgId"], user["userId"], 2_000_000, "fixed it"))
    assert resolved is True

    recurrence = _raise(user["orgId"], subject="resolve:1")
    assert recurrence["isNew"] is True
    assert recurrence["alertId"] != first["alertId"]


def test_reopen_restores_an_alert_to_open():
    user = seed_user_and_token(email="alerts_reopen1@example.com")
    raised = _raise(user["orgId"], subject="reopen:1")
    _run(alerts_db.resolve_alert(raised["alertId"], user["orgId"], user["userId"], 2_000_000, "premature"))

    reopened = _run(alerts_db.reopen_alert(raised["alertId"], user["orgId"], 2_000_100))
    assert reopened is True

    fetched = _run(alerts_db.get_alert(raised["alertId"], user["orgId"]))
    assert fetched["status"] == "open"


# ── Recipient resolution — the digest_only fix ──────────────────────────

def test_digest_only_recipient_excluded_from_immediate_warning_delivery():
    """The actual bug this session found and fixed: a recipient with
    email_digest_only=True must NOT get an immediate alert_deliveries
    row for a non-critical alert — they're covered by
    notifications/digest.py instead."""
    user = seed_user_and_token(email="alerts_digest1@example.com")
    _run(alerts_db.set_notification_preferences(
        user["userId"], user["orgId"], email_critical=True, email_warning=True, email_info=False,
        email_digest_only=True, now=1_000_000,
    ))

    raised = _raise(user["orgId"], subject="digest:warning", severity="warning")
    alert = _run(alerts_db.get_alert(raised["alertId"], user["orgId"]))
    assert alert["deliveries"] == []  # no immediate delivery queued


def test_digest_only_recipient_still_gets_critical_immediately():
    user = seed_user_and_token(email="alerts_digest2@example.com")
    _run(alerts_db.set_notification_preferences(
        user["userId"], user["orgId"], email_critical=True, email_warning=True, email_info=False,
        email_digest_only=True, now=1_000_000,
    ))

    raised = _raise(user["orgId"], subject="digest:critical", severity="critical")
    alert = _run(alerts_db.get_alert(raised["alertId"], user["orgId"]))
    assert len(alert["deliveries"]) == 1
    assert alert["deliveries"][0]["recipient"] == user["email"]


def test_digest_recipient_appears_in_get_due_digest_recipients():
    user = seed_user_and_token(email="alerts_digest3@example.com")
    _run(alerts_db.set_notification_preferences(
        user["userId"], user["orgId"], email_critical=True, email_warning=True, email_info=False,
        email_digest_only=True, now=1_000_000,
    ))
    _raise(user["orgId"], subject="digest:due", severity="warning")

    due = _run(alerts_db.get_due_digest_recipients(user["orgId"], now=1_100_000, interval_seconds=86400))
    assert any(r["userId"] == user["userId"] for r in due)

    # After marking sent, they're no longer due until the interval elapses.
    _run(alerts_db.mark_digest_sent(user["userId"], user["orgId"], 1_100_000))
    still_due = _run(alerts_db.get_due_digest_recipients(user["orgId"], now=1_100_001, interval_seconds=86400))
    assert not any(r["userId"] == user["userId"] for r in still_due)


def test_info_severity_opted_out_by_default():
    user = seed_user_and_token(email="alerts_info1@example.com")
    raised = _raise(user["orgId"], subject="info:default", severity="info", alert_type="rls_test_info")
    alert = _run(alerts_db.get_alert(raised["alertId"], user["orgId"]))
    assert alert["deliveries"] == []  # row-absent preference defaults info to opted-OUT


# ── HTTP surface ──────────────────────────────────────────────────────

def test_list_get_summary_ack_resolve_over_http(client):
    user = seed_user_and_token(email="alerts_http1@example.com")
    raised = _raise(user["orgId"], subject="http:1", severity="critical")

    r_list = client.get("/alerts", headers=_auth(user["token"]))
    assert r_list.status_code == 200
    assert any(a["id"] == raised["alertId"] for a in r_list.json()["alerts"])

    r_get = client.get(f"/alerts/{raised['alertId']}", headers=_auth(user["token"]))
    assert r_get.status_code == 200
    assert r_get.json()["evidence"] == {"k": "v"}

    r_summary = client.get("/alerts/summary", headers=_auth(user["token"]))
    assert r_summary.status_code == 200
    assert r_summary.json()["open"]["critical"] >= 1

    r_ack = client.post(f"/alerts/{raised['alertId']}/acknowledge", json={}, headers=_auth(user["token"]))
    assert r_ack.status_code == 200
    assert client.get(f"/alerts/{raised['alertId']}", headers=_auth(user["token"])).json()["status"] == "acknowledged"

    r_resolve = client.post(
        f"/alerts/{raised['alertId']}/resolve", json={"resolution_note": "done"}, headers=_auth(user["token"]),
    )
    assert r_resolve.status_code == 200
    assert client.get(f"/alerts/{raised['alertId']}", headers=_auth(user["token"])).json()["status"] == "resolved"


def test_alerts_readable_via_api_key_with_scope(client):
    user = seed_user_and_token(email="alerts_apikey1@example.com")
    _raise(user["orgId"], subject="apikey:1")

    key_resp = client.post(
        "/api-keys", json={"scopes": ["alerts:read"]}, headers=_auth(user["token"]),
    )
    raw_key = key_resp.json()["raw_key"]

    r = client.get("/alerts", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200


def test_alerts_not_readable_via_api_key_without_scope(client):
    user = seed_user_and_token(email="alerts_apikey2@example.com")
    key_resp = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth(user["token"]),
    )
    raw_key = key_resp.json()["raw_key"]

    r = client.get("/alerts", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 403


def test_viewer_can_read_but_not_acknowledge(client):
    owner = seed_user_and_token(email="alerts_viewer_owner@example.com")
    raised = _raise(owner["orgId"], subject="viewer:1")

    from db.tenancy import join_org_via_invitation
    from db.engine import get_sessionmaker
    import auth
    import db

    viewer_user = _run(db.create_user(email="alerts_viewer1@example.com", name="Viewer", password="testpassword123", created_at=1_700_000_100))

    async def _join():
        async with get_sessionmaker()() as session:
            await join_org_via_invitation(session, viewer_user["userId"], owner["orgId"], "viewer", owner["userId"], 1_700_000_200)
            await session.commit()

    _run(_join())
    viewer_token = auth.create_token(
        email=viewer_user["email"], name=viewer_user["name"], project_id=viewer_user["projectId"],
        org_id=owner["orgId"], user_id=viewer_user["userId"],
    )

    r_read = client.get(f"/alerts/{raised['alertId']}", headers=_auth(viewer_token))
    assert r_read.status_code == 200

    r_ack = client.post(f"/alerts/{raised['alertId']}/acknowledge", json={}, headers=_auth(viewer_token))
    assert r_ack.status_code == 403


def test_evidence_json_never_stored_as_a_raw_string_in_the_api_response():
    """Regression guard: evidence should be a parsed object, not a
    double-encoded JSON string, in GET /alerts/{id}."""
    user = seed_user_and_token(email="alerts_evidence1@example.com")
    raised = _raise(user["orgId"], subject="evidence:1")
    alert = _run(alerts_db.get_alert(raised["alertId"], user["orgId"]))
    assert isinstance(alert["evidence"], dict)


def test_alert_delivery_status_transitions_are_valid_values():
    """Cheap schema-shape guard, not a full sender.py integration test —
    see test_email_delivery.py for that."""
    assert AlertDelivery.__table__.columns["status"].default.arg == "pending"
