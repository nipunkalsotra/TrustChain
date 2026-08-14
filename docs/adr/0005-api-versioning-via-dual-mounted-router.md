# 0005 — API versioning via a dual-mounted router

**Status:** Accepted

## Context

TrustChain's API had no version prefix at all — every route lived at
the bare path (`/run-agent`, `/audit-log`, ...). Third-party SDK/CLI
consumers need a stable, explicitly-versioned contract to build against
(the plan's own spec calls for `/v1/...`), but the existing frontend and
test suite already depend on the unprefixed paths, and breaking those to
introduce versioning would be a regression in service of a process goal.

## Decision

Route handlers are defined **once**, on a plain `APIRouter()`
(`router` in `main.py`), then mounted **twice** on the app:
`app.include_router(router)` (unprefixed, serves the existing
frontend/tests unchanged) and `app.include_router(router, prefix="/v1")`
(the versioned, canonical path for new/external consumers). Same
handler object, same behavior, reachable at two paths — zero duplicated
logic, zero risk of the two variants drifting apart.

Infra-probe endpoints (`/health`, `/ready`, `/metrics`) stay mounted
directly on `app`, not on `router` — they're not business API surface
and don't need a `/v1/` variant; a load balancer's health check
shouldn't need to know or care about API versioning.

## Alternatives considered

- **Rename every route to live under `/v1/` and update the frontend/tests
  to match.** Rejected: unnecessarily couples "add API versioning" to
  "touch every frontend API call site and every existing test's URL" —
  a much larger, riskier diff for the same outcome, and breaks any
  external consumer who'd already started depending on the unprefixed
  paths during this same development period.
- **A reverse-proxy-level rewrite** (strip `/v1/` before it reaches
  FastAPI). Rejected: adds an infrastructure dependency (nginx/similar)
  this project doesn't otherwise need, and moves versioning logic
  outside the codebase where it's less visible and less testable.

## Consequences

- `app.routes` no longer flatly lists every route after
  `include_router` — modern FastAPI/Starlette wraps an included router
  in a lazy `_IncludedRouter` object rather than immediately flattening
  it. This looked like a bug the first time it was observed (only 9
  entries in `app.routes` after adding 20+ router routes) — it isn't;
  confirmed by making real HTTP requests through `TestClient` to both
  `/leaderboard` and `/v1/leaderboard` (identical 200 responses) and
  checking `/openapi.json` (51 paths, both variants present). Anyone
  debugging routing issues here should verify via real requests, not
  via introspecting `app.routes` directly.
- The unprefixed paths have no deprecation date — they're not a
  transitional shim, they're staying, because the frontend depends on
  them and breaking it isn't the versioning work's job to force.
- Every new endpoint added going forward needs to remember it's defined
  on `router`, not `app`, to get the `/v1/` mount automatically — an
  endpoint accidentally added to `app` directly would silently only
  exist unprefixed.
