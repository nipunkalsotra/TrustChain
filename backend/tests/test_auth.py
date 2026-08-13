def test_signup_returns_token(client):
    r = client.post("/auth/signup", json={"name": "Alice", "email": "alice@test.com", "password": "hunter22"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@test.com"
    assert body["name"] == "Alice"
    assert body["token"]


def test_signup_rejects_short_password(client):
    r = client.post("/auth/signup", json={"name": "Alice", "email": "alice@test.com", "password": "short"})
    assert r.status_code == 422


def test_signup_rejects_invalid_email(client):
    r = client.post("/auth/signup", json={"name": "Alice", "email": "not-an-email", "password": "hunter22"})
    assert r.status_code == 422


def test_duplicate_signup_is_rejected(client):
    payload = {"name": "Alice", "email": "alice@test.com", "password": "hunter22"}
    client.post("/auth/signup", json=payload)
    r = client.post("/auth/signup", json={**payload, "name": "Alice2"})
    assert r.status_code == 409


def test_login_with_correct_password(client):
    client.post("/auth/signup", json={"name": "Alice", "email": "alice@test.com", "password": "hunter22"})
    r = client.post("/auth/login", json={"email": "alice@test.com", "password": "hunter22"})
    assert r.status_code == 200
    assert r.json()["token"]


def test_login_with_wrong_password_fails(client):
    client.post("/auth/signup", json={"name": "Alice", "email": "alice@test.com", "password": "hunter22"})
    r = client.post("/auth/login", json={"email": "alice@test.com", "password": "wrongpassword"})
    assert r.status_code == 401


def test_login_unknown_email_fails(client):
    r = client.post("/auth/login", json={"email": "ghost@test.com", "password": "whatever1"})
    assert r.status_code == 401


def test_run_agent_requires_auth(client):
    r = client.post("/run-agent", json={"task": "do something"})
    assert r.status_code == 401


def test_run_agent_rejects_garbage_token(client):
    r = client.post("/run-agent", json={"task": "do something"}, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_run_agent_rejects_missing_bearer_prefix(client):
    signup = client.post("/auth/signup", json={"name": "Alice", "email": "alice@test.com", "password": "hunter22"})
    token = signup.json()["token"]
    r = client.post("/run-agent", json={"task": "do something"}, headers={"Authorization": token})
    assert r.status_code == 401
