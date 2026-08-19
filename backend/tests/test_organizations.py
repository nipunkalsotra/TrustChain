"""
tests/test_organizations.py — organization and project CRUD over the
real HTTP surface (Phase 3 §4.4/§9.2-9.3).
"""

from tests.conftest import seed_user_and_token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_me_reflects_the_auto_provisioned_personal_org(client):
    user = seed_user_and_token(email="orgs_me@example.com", name="Org Tester")
    r = client.get("/me", headers=_auth(user["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "orgs_me@example.com"
    assert body["active"]["role"] == "owner"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"
    assert body["memberships"][0]["org"]["name"] == "Org Tester's Organization"
    assert [p["name"] for p in body["memberships"][0]["projects"]] == ["Default"]


def test_create_org_provisions_project_and_owner_membership(client):
    user = seed_user_and_token(email="orgs_create@example.com")
    r = client.post(
        "/orgs", json={"name": "Acme Corp", "project_name": "prod"}, headers=_auth(user["token"]),
    )
    assert r.status_code == 200
    org_id = r.json()["orgId"]

    listed = client.get("/orgs", headers=_auth(user["token"])).json()["orgs"]
    names = {o["id"]: o["name"] for o in listed}
    assert names[org_id] == "Acme Corp"
    # The user now belongs to TWO orgs — their auto-provisioned personal
    # one, and the one they just explicitly created.
    assert len(listed) == 2


def test_rename_org_requires_admin(client):
    owner = seed_user_and_token(email="orgs_rename_owner@example.com")
    member = seed_user_and_token(email="orgs_rename_member@example.com")

    r_ok = client.patch(f"/orgs/{owner['orgId']}", json={"name": "Renamed"}, headers=_auth(owner["token"]))
    assert r_ok.status_code == 200
    assert client.get(f"/orgs/{owner['orgId']}", headers=_auth(owner["token"])).json()["name"] == "Renamed"

    # A user with no membership in owner's org at all gets 403, not 404 —
    # existence of the org isn't the question being answered.
    r_forbidden = client.patch(f"/orgs/{owner['orgId']}", json={"name": "Hijacked"}, headers=_auth(member["token"]))
    assert r_forbidden.status_code == 403


def test_cannot_delete_your_only_org(client):
    user = seed_user_and_token(email="orgs_only@example.com")
    r = client.delete(f"/orgs/{user['orgId']}", headers=_auth(user["token"]))
    assert r.status_code == 400
    assert r.json()["error_code"] == "cannot_delete_only_org"


def test_delete_org_excludes_it_from_subsequent_reads(client):
    user = seed_user_and_token(email="orgs_delete@example.com")
    second = client.post("/orgs", json={"name": "Disposable Org"}, headers=_auth(user["token"])).json()

    r = client.delete(f"/orgs/{second['orgId']}", headers=_auth(user["token"]))
    assert r.status_code == 200

    listed = client.get("/orgs", headers=_auth(user["token"])).json()["orgs"]
    assert second["orgId"] not in {o["id"] for o in listed}

    r_get = client.get(f"/orgs/{second['orgId']}", headers=_auth(user["token"]))
    assert r_get.status_code == 404


def test_create_and_list_projects(client):
    user = seed_user_and_token(email="orgs_projects@example.com")
    r = client.post(
        f"/orgs/{user['orgId']}/projects", json={"name": "staging", "environment": "test"},
        headers=_auth(user["token"]),
    )
    assert r.status_code == 200
    project_id = r.json()["id"]

    listed = client.get(f"/orgs/{user['orgId']}/projects", headers=_auth(user["token"])).json()["projects"]
    names = {p["id"]: (p["name"], p["environment"]) for p in listed}
    assert names[project_id] == ("staging", "test")
    assert len(listed) == 2  # Default + staging


def test_cannot_delete_the_last_project(client):
    user = seed_user_and_token(email="orgs_last_project@example.com")
    me = client.get("/me", headers=_auth(user["token"])).json()
    default_project_id = me["memberships"][0]["projects"][0]["id"]

    r = client.delete(f"/projects/{default_project_id}", headers=_auth(user["token"]))
    assert r.status_code == 400
    assert r.json()["error_code"] == "cannot_delete_last_project"


def test_delete_a_non_last_project_succeeds(client):
    user = seed_user_and_token(email="orgs_delete_project@example.com")
    created = client.post(
        f"/orgs/{user['orgId']}/projects", json={"name": "temp", "environment": "test"},
        headers=_auth(user["token"]),
    ).json()

    r = client.delete(f"/projects/{created['id']}", headers=_auth(user["token"]))
    assert r.status_code == 200

    listed = client.get(f"/orgs/{user['orgId']}/projects", headers=_auth(user["token"])).json()["projects"]
    assert created["id"] not in {p["id"] for p in listed}


def test_switch_project_mints_a_token_scoped_to_a_different_project(client):
    user = seed_user_and_token(email="orgs_switch@example.com")
    other = client.post(
        f"/orgs/{user['orgId']}/projects", json={"name": "other", "environment": "live"},
        headers=_auth(user["token"]),
    ).json()

    r = client.post("/auth/switch-project", json={"project_id": other["id"]}, headers=_auth(user["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["projectId"] == other["id"]
    assert body["role"] == "owner"

    # The new token actually works and is scoped to the new project.
    r2 = client.get("/runs", headers=_auth(body["token"]))
    assert r2.status_code == 200


def test_switch_project_denied_for_a_project_you_have_no_membership_in(client):
    user_a = seed_user_and_token(email="orgs_switch_a@example.com")
    user_b = seed_user_and_token(email="orgs_switch_b@example.com")

    r = client.post("/auth/switch-project", json={"project_id": user_b["projectId"]}, headers=_auth(user_a["token"]))
    assert r.status_code == 403
