"""API deprecation headers — see docs/api-deprecation-policy.md for the
policy this implements (what qualifies, notice period, how a route
moves through this list).

`DEPRECATED_ROUTES` is empty by design today: nothing in this API is
deprecated yet (see ADR-0005 — the unprefixed legacy routes are a
permanent alias, not a transitional shim, so the dual-mount itself is
NOT a deprecation). This module exists so that WHEN something is
actually deprecated, the `Deprecation`/`Sunset`/`Link` headers appear
at exactly one place instead of being hand-added to scattered route
handlers and inevitably drifting out of sync with each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from typing import Optional

from starlette.requests import Request
from starlette.responses import Response


@dataclass(frozen=True)
class DeprecatedRoute:
    # Must match an APIRoute's `.path` template exactly (e.g.
    # "/v1/runs/{run_id}", not the raw request path with the id filled
    # in) — see `_matching_entry` below, which compares against
    # `request.scope["route"].path`, the same field `_metrics_middleware`
    # already uses in main.py for exactly this "don't leak path params
    # into a label/header" reason.
    path: str
    # Timezone-aware. The date after which the route may stop working
    # (RFC 8594's `Sunset` header) — not merely "the date we announced
    # the deprecation."
    sunset: datetime
    # Direct replacement, if one exists, as an absolute URL (e.g. an
    # OpenAPI-doc anchor or a new endpoint's docs page) — emitted as a
    # `Link: <url>; rel="successor-version"` header per RFC 8594 §6.1's
    # example usage. None if there's no direct 1:1 replacement (e.g. a
    # capability being removed outright).
    successor: Optional[str] = None
    # None = deprecated for all methods on this path.
    methods: Optional[frozenset] = None


# Add an entry here — and a matching row in docs/api-deprecation-policy.md's
# "Active deprecations" table — when a route is actually deprecated. The
# two are meant to be kept in lockstep by hand; each file's docstring/intro
# says so and points at the other.
DEPRECATED_ROUTES: list[DeprecatedRoute] = []


def _matching_entry(
    route_path: str, method: str, deprecated_routes: list[DeprecatedRoute]
) -> Optional[DeprecatedRoute]:
    for entry in deprecated_routes:
        if entry.path != route_path:
            continue
        if entry.methods is not None and method.upper() not in entry.methods:
            continue
        return entry
    return None


def headers_for_route(
    route_path: str,
    method: str,
    deprecated_routes: Optional[list[DeprecatedRoute]] = None,
) -> dict[str, str]:
    """Pure function: route template + method -> headers to add, or {}
    if that route/method isn't in `deprecated_routes`. Split out from
    the middleware below so it's testable without a real request/
    response object.

    `deprecated_routes` defaults to the CURRENT value of the module-level
    DEPRECATED_ROUTES (looked up here, not captured as a mutable default
    argument at def time) so that reassigning it — e.g.
    `monkeypatch.setattr(deprecation, "DEPRECATED_ROUTES", [...])` in a
    test, or a real future deprecation being added — takes effect
    immediately, including for callers (the middleware) that already
    hold a reference to this function.
    """
    if deprecated_routes is None:
        deprecated_routes = DEPRECATED_ROUTES
    entry = _matching_entry(route_path, method, deprecated_routes)
    if entry is None:
        return {}

    headers = {
        # draft-ietf-httpapi-deprecation-header: a bare "true" is valid
        # and — unlike encoding a second "deprecated since" date next
        # to Sunset's "stops working on" date — matches how most real
        # clients that check this header at all just check for its
        # presence.
        "Deprecation": "true",
        "Sunset": format_datetime(entry.sunset, usegmt=True),
    }
    if entry.successor:
        headers["Link"] = f'<{entry.successor}>; rel="successor-version"'
    return headers


async def add_deprecation_headers(request: Request, response: Response) -> None:
    """Called from main.py's middleware stack after the route has been
    resolved (request.scope["route"] is only populated once routing has
    actually run, i.e. after call_next returns)."""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path is None:
        return
    for key, value in headers_for_route(route_path, request.method).items():
        response.headers[key] = value
