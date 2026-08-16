"""Tests for deprecation.py — the Deprecation/Sunset/Link header
mechanism described in docs/api-deprecation-policy.md. `headers_for_route`
is pure-logic (unit); the rest goes through a real TestClient/FastAPI
app so the middleware wiring in main.py (route resolution, header
propagation) is verified too, not just the function in isolation.
"""

from datetime import datetime, timezone

import deprecation


SUNSET = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_headers_for_route_empty_by_default():
    # DEPRECATED_ROUTES ships empty — nothing is deprecated today.
    assert deprecation.headers_for_route("/health", "GET") == {}


def test_headers_for_route_no_match_returns_empty():
    routes = [deprecation.DeprecatedRoute(path="/v1/old-thing", sunset=SUNSET)]
    assert deprecation.headers_for_route("/v1/other-thing", "GET", routes) == {}


def test_headers_for_route_match_sets_deprecation_and_sunset():
    routes = [deprecation.DeprecatedRoute(path="/v1/old-thing", sunset=SUNSET)]
    headers = deprecation.headers_for_route("/v1/old-thing", "GET", routes)
    assert headers["Deprecation"] == "true"
    # RFC 8594 Sunset uses the same IMF-fixdate format as HTTP's Date
    # header — round-trip it through email.utils to confirm the format
    # is actually valid, not just "looks like a date".
    from email.utils import parsedate_to_datetime

    assert parsedate_to_datetime(headers["Sunset"]) == SUNSET
    assert "Link" not in headers


def test_headers_for_route_includes_link_when_successor_set():
    routes = [
        deprecation.DeprecatedRoute(
            path="/v1/old-thing", sunset=SUNSET, successor="https://example.com/docs/v2/new-thing"
        )
    ]
    headers = deprecation.headers_for_route("/v1/old-thing", "GET", routes)
    assert headers["Link"] == '<https://example.com/docs/v2/new-thing>; rel="successor-version"'


def test_headers_for_route_respects_method_restriction():
    routes = [
        deprecation.DeprecatedRoute(path="/v1/old-thing", sunset=SUNSET, methods=frozenset({"DELETE"}))
    ]
    assert deprecation.headers_for_route("/v1/old-thing", "GET", routes) == {}
    assert deprecation.headers_for_route("/v1/old-thing", "DELETE", routes) != {}


def test_headers_for_route_none_methods_matches_any_method():
    routes = [deprecation.DeprecatedRoute(path="/v1/old-thing", sunset=SUNSET, methods=None)]
    assert deprecation.headers_for_route("/v1/old-thing", "POST", routes) != {}
    assert deprecation.headers_for_route("/v1/old-thing", "GET", routes) != {}


def test_reassigning_module_level_deprecated_routes_takes_effect(monkeypatch):
    # Guards against the mutable-default-argument bug this function was
    # written to avoid: reassigning the module attribute (not mutating
    # the same list object in place) must still be picked up.
    monkeypatch.setattr(
        deprecation,
        "DEPRECATED_ROUTES",
        [deprecation.DeprecatedRoute(path="/health", sunset=SUNSET)],
    )
    assert deprecation.headers_for_route("/health", "GET") != {}


# ── Through the real app/middleware ─────────────────────────────────────


def test_live_response_has_no_deprecation_headers_today(client):
    # Nothing is deprecated in production code — confirms the middleware
    # is genuinely a no-op right now, not just that the function is.
    #
    # /ready, not /health: /health depends on the V1 bridge (real
    # PRIVATE_KEY/MONAD_RPC_URL), which get_bridge_or_503's docstring
    # documents as deliberately unconfigured in CI — asserting 200 there
    # only passed locally because dev's own backend/.env happens to carry
    # a real PRIVATE_KEY. /ready only depends on Postgres (always present
    # here) and always returns 200 regardless of chain/bridge state.
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert "Deprecation" not in resp.headers
    assert "Sunset" not in resp.headers


def test_live_response_gets_deprecation_headers_when_route_is_listed(client, monkeypatch):
    monkeypatch.setattr(
        deprecation,
        "DEPRECATED_ROUTES",
        [
            deprecation.DeprecatedRoute(
                path="/ready", sunset=SUNSET, successor="https://example.com/docs/v2/health"
            )
        ],
    )
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.headers["Deprecation"] == "true"
    assert resp.headers["Sunset"] == "Fri, 01 Jan 2027 00:00:00 GMT"
    assert resp.headers["Link"] == '<https://example.com/docs/v2/health>; rel="successor-version"'


def test_live_response_unaffected_route_stays_clean_when_another_route_is_deprecated(client, monkeypatch):
    monkeypatch.setattr(
        deprecation,
        "DEPRECATED_ROUTES",
        [deprecation.DeprecatedRoute(path="/health", sunset=SUNSET)],
    )
    resp = client.get("/ready")
    assert "Deprecation" not in resp.headers
