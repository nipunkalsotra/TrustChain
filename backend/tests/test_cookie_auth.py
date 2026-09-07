"""
Cookie-based browser auth + CSRF (P1) — the web frontend no longer holds a
token in localStorage; it authenticates via the tc_access/tc_refresh
HttpOnly cookies POST /auth/signup and /auth/login now set alongside their
unchanged JSON body (see main.py's own comment on that route, and
refresh.py's module docstring for the cookie design). Bearer/API-key
callers (SDK/CLI) are entirely unaffected — see test_auth.py for that
coverage, which stays green untouched by any of this.
"""

import time

from tests.conftest import seed_user_and_token


def _signup(client, email: str) -> dict:
    r = client.post(
        "/auth/signup",
        json={"name": "Cookie User", "email": email, "password": "CorrectHorseBattery9!"},
    )
    assert r.status_code == 200
    return r


def test_signup_sets_session_cookies_with_correct_attributes(client):
    r = _signup(client, "cookie_signup@example.com")

    cookie_names = {c.name for c in client.cookies.jar}
    assert {"tc_access", "tc_refresh", "tc_csrf"} <= cookie_names

    # httpx's cookiejar exposes the underlying http.cookiejar.Cookie objects
    # — httponly isn't a standard attribute there, so check it via the raw
    # Set-Cookie response headers instead, which is what a browser actually
    # parses this from.
    set_cookie_headers = r.headers.get_list("set-cookie")
    by_name = {h.split("=", 1)[0]: h for h in set_cookie_headers}
    assert "HttpOnly" in by_name["tc_access"]
    assert "HttpOnly" in by_name["tc_refresh"]
    # Deliberately NOT HttpOnly — the frontend's own JS has to read this one
    # to echo it back as X-CSRF-Token (double-submit design).
    assert "HttpOnly" not in by_name["tc_csrf"]

    # Body shape is UNCHANGED (SDK/CLI compatibility — trustchain-cli's
    # login command parses body["token"] directly).
    body = r.json()
    assert set(body.keys()) == {"token", "name", "email"}


def test_cookie_only_request_authenticates_with_no_authorization_header(client):
    _signup(client, "cookie_only_auth@example.com")
    # client.cookies now carries tc_access from the signup above; no
    # Authorization header is set anywhere in this test.
    r = client.get("/runs")
    assert r.status_code == 200


def test_unsafe_request_via_cookie_without_csrf_header_is_rejected(client):
    _signup(client, "csrf_missing@example.com")
    r = client.post("/auth/logout")
    assert r.status_code == 403
    assert r.json()["error_code"] == "csrf_token_invalid"


def test_unsafe_request_via_cookie_with_correct_csrf_header_succeeds(client):
    _signup(client, "csrf_correct@example.com")
    csrf = next(c.value for c in client.cookies.jar if c.name == "tc_csrf")
    r = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_unsafe_request_via_cookie_with_wrong_csrf_header_is_rejected(client):
    _signup(client, "csrf_wrong@example.com")
    r = client.post("/auth/logout", headers={"X-CSRF-Token": "not-the-real-token"})
    assert r.status_code == 403


def test_bearer_authenticated_request_never_needs_csrf(client):
    """The whole point of CSRF protection is defending a credential the
    browser attaches AUTOMATICALLY (cookies) — a request that already
    carries an explicit Authorization header couldn't have been forged by
    a third-party page the same way, so it's exempt entirely, cookies
    present or not."""
    user = seed_user_and_token(email="csrf_bearer_exempt@example.com")
    # No cookies at all in this call — a pure SDK/CLI-style Bearer request.
    r = client.post(
        "/run-agent",
        json={"task": "bearer csrf exemption test"},
        headers={"Authorization": f"Bearer {user['token']}"},
    )
    assert r.status_code == 200


def test_refresh_via_cookie_rotates_the_access_cookie(client):
    _signup(client, "cookie_refresh@example.com")
    old_refresh = next(c.value for c in client.cookies.jar if c.name == "tc_refresh")
    csrf = next(c.value for c in client.cookies.jar if c.name == "tc_csrf")

    r = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200

    # The access token's own claims (project_id/org_id/user_id/iat/exp) can
    # legitimately be byte-identical to the pre-refresh one if both were
    # minted within the same integer second — refresh.py's raw refresh
    # token (secrets.token_urlsafe(32), no such collision possibility) is
    # what actually proves rotation happened.
    new_refresh = next(c.value for c in client.cookies.jar if c.name == "tc_refresh")
    assert new_refresh != old_refresh

    # The new access cookie is immediately usable.
    r2 = client.get("/runs")
    assert r2.status_code == 200


def test_refresh_also_works_via_json_body_for_sdk_cli_callers(client):
    """SDK/CLI callers never hold cookies at all — POST /auth/refresh must
    keep accepting {"refresh_token": "..."} in the body exactly as before
    P1 (see refresh.py's issue_token_pair/rotate_refresh_token, unchanged
    by this feature)."""
    user = seed_user_and_token(email="refresh_body_only@example.com")
    r = client.post("/auth/token-pair", headers={"Authorization": f"Bearer {user['token']}"})
    assert r.status_code == 200
    refresh_token = r.json()["refresh_token"]

    r2 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert r2.status_code == 200
    assert "access_token" in r2.json()


def test_logout_clears_all_three_cookies_and_revokes_the_refresh_family(client):
    _signup(client, "cookie_logout@example.com")
    csrf = next(c.value for c in client.cookies.jar if c.name == "tc_csrf")
    old_refresh = next(c.value for c in client.cookies.jar if c.name == "tc_refresh")

    r = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200

    set_cookie_headers = r.headers.get_list("set-cookie")
    for h in set_cookie_headers:
        assert "Max-Age=0" in h

    # The revoked refresh token must no longer rotate — proves logout
    # actually revoked the family server-side, not just cleared cookies
    # client-side (see refresh.py's revoke_family_for_token).
    r2 = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401


def test_expired_access_cookie_triggers_a_401_that_a_silent_refresh_would_recover_from(client, monkeypatch):
    """Doesn't exercise the frontend's own retry logic (that's a browser-
    side concern, see frontend/lib/api.ts's apiFetch) — this just proves
    the SERVER half of that contract: an expired tc_access cookie 401s
    (rather than, say, 500ing or silently treating it as unauthenticated
    in some other way), and the still-valid tc_refresh cookie can recover
    a session from exactly that state via POST /auth/refresh."""
    import auth

    _signup(client, "cookie_expired_access@example.com")
    csrf = next(c.value for c in client.cookies.jar if c.name == "tc_csrf")

    # Mint a tc_access cookie value as if it were minted 20 minutes ago —
    # past the 15-minute TTL — and swap it into the client's cookie jar.
    real_time = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() - 20 * 60)
    import refresh as refresh_module

    expired_access = refresh_module._short_lived_access_token(
        "cookie_expired_access@example.com", "Cookie User", 1, 1, 1, int(real_time() - 20 * 60)
    )
    monkeypatch.setattr(auth.time, "time", real_time)
    # Must match the domain/path TestClient's own Set-Cookie responses use
    # ("testserver.local", "/") — client.cookies.set()'s own default
    # domain ("") creates a SEPARATE jar entry alongside the real one
    # rather than replacing it, and both get sent on the next request,
    # which one wins is then undefined from this test's point of view.
    client.cookies.set("tc_access", expired_access, domain="testserver.local", path="/")

    r = client.get("/runs")
    assert r.status_code == 401

    r2 = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r2.status_code == 200

    r3 = client.get("/runs")
    assert r3.status_code == 200
