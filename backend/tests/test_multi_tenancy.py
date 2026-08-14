"""
Integration tests for the multi-tenancy HTTP surface: API key issuance/
listing/revocation, the additive short-lived-access + rotating-refresh
token flow, and API-key-based authentication actually working end-to-end
through FastAPI's dependency injection — not just the underlying db/
tenancy.py and refresh.py functions (covered directly elsewhere).
"""

import asyncio

from tests.conftest import seed_user_and_token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── API key management ──────────────────────────────────────────────────

def test_create_list_and_revoke_api_key(client):
    user = seed_user_and_token()

    r = client.post(
        "/api-keys", json={"scopes": ["runs:read", "runs:write"]}, headers=_auth_headers(user["token"])
    )
    assert r.status_code == 200
    body = r.json()
    assert body["raw_key"].startswith("tc_live_")
    assert body["last_four"] == body["raw_key"][-4:]
    key_id = body["id"]

    r2 = client.get("/api-keys", headers=_auth_headers(user["token"]))
    assert r2.status_code == 200
    keys = r2.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["id"] == key_id
    assert "raw_key" not in keys[0]  # never re-shown after creation

    r3 = client.delete(f"/api-keys/{key_id}", headers=_auth_headers(user["token"]))
    assert r3.status_code == 200

    # revoking an already-revoked key is a 404, not a silent success
    r4 = client.delete(f"/api-keys/{key_id}", headers=_auth_headers(user["token"]))
    assert r4.status_code == 404


def test_create_api_key_rejects_unknown_scope(client):
    user = seed_user_and_token()
    r = client.post("/api-keys", json={"scopes": ["not:a:real:scope"]}, headers=_auth_headers(user["token"]))
    assert r.status_code == 400


def test_api_key_management_requires_auth(client):
    assert client.post("/api-keys", json={"scopes": []}).status_code == 401
    assert client.get("/api-keys").status_code == 401


def test_cannot_revoke_another_projects_api_key(client):
    alice = seed_user_and_token("alice-apikey@example.com", "Alice")
    bob = seed_user_and_token("bob-apikey@example.com", "Bob")

    created = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth_headers(alice["token"])
    ).json()

    r = client.delete(f"/api-keys/{created['id']}", headers=_auth_headers(bob["token"]))
    assert r.status_code == 404  # not found *for Bob's project* — not a 403 that would confirm it exists


# ── API-key-based authentication on project-scoped endpoints ───────────────

def test_api_key_can_start_a_run_with_correct_scope(client, monkeypatch):
    """An SDK-driven third-party agent authenticates with an API key, not
    a human JWT — POST /run-agent must accept either."""
    user = seed_user_and_token()
    key = client.post(
        "/api-keys", json={"scopes": ["runs:write"]}, headers=_auth_headers(user["token"])
    ).json()

    async def _fake_run_pipeline(task, run_id=None, bridge=None):
        yield {"type": "run_started", "runId": run_id or "run_x", "task": task}

    import main
    monkeypatch.setattr(main, "run_pipeline", _fake_run_pipeline)

    r = client.post("/run-agent", json={"task": "do a thing"}, headers=_auth_headers(key["raw_key"]))
    assert r.status_code == 200
    assert r.json()["status"] == "started"


def test_api_key_without_scope_is_forbidden(client):
    user = seed_user_and_token()
    key = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth_headers(user["token"])
    ).json()

    r = client.post("/run-agent", json={"task": "do a thing"}, headers=_auth_headers(key["raw_key"]))
    assert r.status_code == 403


def test_revoked_api_key_is_rejected(client):
    user = seed_user_and_token()
    key = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth_headers(user["token"])
    ).json()
    client.delete(f"/api-keys/{key['id']}", headers=_auth_headers(user["token"]))

    r = client.get("/runs", headers=_auth_headers(key["raw_key"]))
    assert r.status_code == 401


def test_api_key_sees_only_its_own_project_runs(client):
    import db

    alice = seed_user_and_token("alice-scope@example.com", "Alice")
    bob = seed_user_and_token("bob-scope@example.com", "Bob")
    asyncio.run(db.create_run("run_alice_key_test", alice["projectId"], "task", None, 1000))

    alice_key = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth_headers(alice["token"])
    ).json()
    bob_key = client.post(
        "/api-keys", json={"scopes": ["runs:read"]}, headers=_auth_headers(bob["token"])
    ).json()

    r_alice = client.get("/runs", headers=_auth_headers(alice_key["raw_key"]))
    assert r_alice.json()["total"] == 1

    r_bob = client.get("/runs", headers=_auth_headers(bob_key["raw_key"]))
    assert r_bob.json()["total"] == 0


# ── short-lived-access + rotating-refresh flow ──────────────────────────

def test_token_pair_issue_refresh_and_logout(client):
    user = seed_user_and_token()

    r = client.post("/auth/token-pair", headers=_auth_headers(user["token"]))
    assert r.status_code == 200
    pair1 = r.json()
    assert pair1["expires_in"] == 15 * 60

    r2 = client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    assert r2.status_code == 200
    pair2 = r2.json()
    assert pair2["refresh_token"] != pair1["refresh_token"]

    # the new access token actually authenticates
    r3 = client.get("/runs", headers=_auth_headers(pair2["access_token"]))
    assert r3.status_code == 200

    # reusing the OLD (already-rotated) refresh token is rejected
    r4 = client.post("/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    assert r4.status_code == 401

    # ...and reuse detection revoked the WHOLE family, so even the
    # legitimate pair2 refresh token is now dead too
    r5 = client.post("/auth/refresh", json={"refresh_token": pair2["refresh_token"]})
    assert r5.status_code == 401


def test_logout_revokes_the_refresh_token(client):
    user = seed_user_and_token()
    pair = client.post("/auth/token-pair", headers=_auth_headers(user["token"])).json()

    r = client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200

    r2 = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 401


def test_refresh_with_garbage_token_is_rejected(client):
    r = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401
