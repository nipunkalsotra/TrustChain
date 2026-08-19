# 0019 — JWT membership liveness check

**Status:** Accepted

## Context

`auth.py`'s primary session JWT is deliberately 7-day-lived and
self-contained — it embeds `project_id`/`org_id` at issuance and is
never re-resolved against the database on subsequent requests, by
design (avoiding a DB round trip to authenticate every call). That
design's safety depended entirely on a fact that stopped being true this
phase: "a user has exactly one org/project, and can never lose access to
it." Phase 3 adds real member removal and role changes. Without a fix,
someone removed from an org — or an attacker who stole a token from
someone later removed — keeps working against that org for up to seven
days on an already-issued token. That is a real privilege-revocation
gap, not a theoretical one.

## Decision

`auth.py::_check_membership_still_live` runs on every JWT-authenticated
request (`get_current_user`/`get_current_principal`'s JWT branch only —
never the API-key branch, which has its own independent revocation via
`ApiKey.revoked_at`) and resolves `(user_id, org_id) -> role` through
`membership_cache.get_role_cached`. A `None` result (no membership found)
401s with `MEMBERSHIP_REVOKED`.

The check is Redis-cached with a short TTL
(`membership_cache_ttl_seconds`, default 60s) as a courtesy default, but
**the TTL is not the primary revocation mechanism** — every operation
that changes or removes a membership (`db/orgs.py::change_role`/
`remove_member`/`transfer_ownership`, called from `main.py`'s member
endpoints) explicitly calls `membership_cache.invalidate()` in the same
request, deleting the cache key immediately. Revocation is therefore
effectively instant for the common case; the TTL is only a worst-case
bound for the rare case where invalidation is somehow missed.

Cache failures fail **closed through to Postgres**, not open: if Redis
is unreachable, `get_role_cached` falls through to a real database read
rather than either treating the request as still-authorized (a security
regression) or as revoked (an availability regression caused by
infrastructure trouble alone). This is the opposite failure mode from
`rate_limit.py`'s Redis usage, which fails *open* deliberately — a rate
limit is a fairness mechanism where over-permitting under Redis outage
is the acceptable side; a membership check is an authorization decision,
where it is not.

## Alternatives considered

- **Shorten the primary token's TTL** (the plan's original spec called
  for a 15-minute access token). Rejected for the same reason `auth.py`'s
  own pre-existing module docstring already gives: the deployed frontend
  has no silent-refresh logic, and shortening the token with nothing to
  renew it would force real logged-in users out unpredictably — a
  regression this phase must not introduce. (The plan's actual
  short-lived-access + rotating-refresh mechanism, `refresh.py`, already
  exists as fully additive capability for whenever the frontend adopts
  it — this ADR's fix is orthogonal and works regardless.)
- **A full revocation list / token blocklist.** More moving parts than
  the problem needs — a membership-liveness check accomplishes the same
  outcome (a revoked user's next request fails) without needing to track
  individual token identifiers at all; it asks "is this STILL true"
  rather than "was this SPECIFIC token blocked."
- **No caching, a Postgres read on every authenticated request.**
  Simpler, but reintroduces exactly the per-request DB round trip the
  original embed-in-JWT design was chosen to avoid — measured cost is a
  real k6 concern for a system whose SDK write path (`POST /steps`) is
  meant to be cheap and frequent.

## Consequences

- `auth.Principal` gained a `user_id` field (`None` for an API-key
  principal) as a side effect — needed so endpoints reachable by either
  credential type could still run this human-only check. See ADR-0013's
  Consequences for how that field also enabled `GET /alerts`'
  dual-credential support.
- `MEMBERSHIP_CACHE_TOTAL{result="hit"|"miss"|"invalidated"}`
  (`observability.py`) is the operational signal for this mechanism — a
  rising miss rate under steady traffic points at TTL churn (harmless);
  a rising `invalidated` count with no corresponding drop in later hits
  would point at the cache-fallthrough path itself misbehaving.
- Every membership-mutating code path must remember to call
  `membership_cache.invalidate()` — a future one that forgets degrades
  to "revocation takes up to `membership_cache_ttl_seconds`" rather than
  breaking outright (the TTL is still a real ceiling), but that's a
  silent latency regression on a security property, worth flagging in
  review whenever a new membership-mutating endpoint is added.
