"""
membership_cache.py — Redis-backed membership liveness check (Phase 3 §4.3).

WHY THIS EXISTS: the primary JWT is 7-day-lived and self-contained — it
embeds project_id/org_id at issuance and auth.py deliberately never
re-resolves them per request (see that module's own docstring on why:
avoiding a DB round trip on every authenticated call). That was harmless
when every user had exactly one org they could never be removed from.
Phase 3 adds real removal, role changes, and multi-org membership, so a
stale-but-unexpired JWT asserting authority a person no longer has
becomes a real privilege-revocation gap, not a theoretical one — someone
removed from an org keeps working for up to 7 days on their existing
token.

THE FIX, deliberately NOT "shorten the token TTL": that reopens the exact
problem auth.py's docstring already explains was rejected (the deployed
frontend has no silent-refresh logic; shortening the primary token forces
real users out unpredictably). Instead: cache the (user_id, org_id) ->
role lookup in Redis with a short TTL as a courtesy default, but — this
is the part that actually matters — every mutation that changes or
revokes a membership (db/orgs.py's change_role/remove_member, org
deletion) explicitly DELETES the cache key in the same request, so
revocation is effectively IMMEDIATE for anyone hitting the API again
after the change; the TTL is only a worst-case bound for the case where
invalidation is somehow missed, not the primary revocation mechanism.

FAILS CLOSED THROUGH TO POSTGRES, not open like rate_limit.py: a
membership check is an authorization decision, not a soft rate-limiting
courtesy — if Redis is unreachable, the correct fallback is the real
source of truth (a Postgres read via permissions.get_role), not skipping
the check.
"""

from typing import Optional

import observability
from permissions import get_role
from redis_client import get_redis

_KEY_TTL_SECONDS_DEFAULT = 60


def _cache_key(user_id: int, org_id: int) -> str:
    return f"membership:{user_id}:{org_id}"


# Sentinel stored for "confirmed no membership" so a revoked user doesn't
# hammer Postgres on every request until the TTL naturally expires — see
# invalidate() for why this is still safe to combine with immediate
# invalidation on the ADD side (a fresh membership explicitly clears this).
_NO_MEMBERSHIP_SENTINEL = "__none__"


async def get_role_cached(user_id: int, org_id: int, ttl_seconds: int = _KEY_TTL_SECONDS_DEFAULT) -> Optional[str]:
    """Returns the caller's current role in org_id, or None if they hold
    no membership there — used by auth.py's JWT path on every
    authenticated request. Cache-then-Postgres, fails closed through to
    Postgres on any Redis error rather than treating an unreachable cache
    as "membership still valid" (see module docstring)."""
    key = _cache_key(user_id, org_id)
    try:
        r = get_redis()
        cached = await r.get(key)
        if cached is not None:
            observability.MEMBERSHIP_CACHE_TOTAL.labels(result="hit").inc()
            return None if cached == _NO_MEMBERSHIP_SENTINEL else cached
    except Exception:
        # Redis unreachable — fall through to the real check below rather
        # than either failing open (treat as still a member) or failing
        # closed on infrastructure trouble alone (treat as revoked).
        cached = "__redis_error__"

    observability.MEMBERSHIP_CACHE_TOTAL.labels(result="miss").inc()
    role = await get_role(user_id, org_id)

    try:
        r = get_redis()
        await r.set(key, role if role is not None else _NO_MEMBERSHIP_SENTINEL, ex=ttl_seconds)
    except Exception:
        pass  # best-effort write-through; a failed cache write doesn't affect this request's own correctness

    return role


async def invalidate(user_id: int, org_id: int) -> None:
    """Called by every membership-mutating operation (db/orgs.py's
    change_role/remove_member/transfer_ownership, and org deletion) in
    the SAME request that performs the change — this is what makes
    revocation effectively immediate rather than bounded only by the
    cache TTL."""
    try:
        r = get_redis()
        await r.delete(_cache_key(user_id, org_id))
        observability.MEMBERSHIP_CACHE_TOTAL.labels(result="invalidated").inc()
    except Exception:
        pass  # best-effort — the TTL is the worst-case bound if this is ever missed
