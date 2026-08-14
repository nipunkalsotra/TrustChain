"""
rate_limit.py — Redis token-bucket rate limiting (plan §11.4: "Redis
token buckets, applied per API key and per IP, with distinct budgets for
read and write paths... POST /runs receives a strict limit because each
call spends both LLM credits and gas — real money").

Atomic via a single Lua script (EVAL) — one round trip, no read-then-write
race between two concurrent requests against the same bucket key.

Fails OPEN: if Redis itself is unreachable, requests are allowed through
rather than the whole API going down because its rate limiter's own
dependency hiccuped. A rate limiter is a defense-in-depth layer, not the
last line of correctness — LLM token budgets and gas cost caps (not yet
implemented; see the plan's O10) are what actually bound worst-case spend.
"""

import time

from fastapi import HTTPException

import observability
from redis_client import get_redis

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])  -- tokens per second
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    updated_at = now
end

local elapsed = now - updated_at
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) + 1)

return {allowed, tokens}
"""


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds:.1f}s")


async def check_rate_limit(bucket_key: str, capacity: float, refill_per_second: float, cost: float = 1.0) -> None:
    """Raises RateLimitExceeded if `bucket_key` doesn't have `cost` tokens
    available right now; otherwise consumes them silently."""
    r = get_redis()
    now = time.time()
    try:
        allowed, remaining = await r.eval(_TOKEN_BUCKET_LUA, 1, bucket_key, capacity, refill_per_second, now, cost)
    except Exception:
        return  # fail open — see module docstring

    if not int(allowed):
        retry_after = max(0.1, (cost - float(remaining)) / refill_per_second)
        raise RateLimitExceeded(retry_after)


async def enforce_rate_limit(bucket_key: str, capacity: float, refill_per_second: float, cost: float = 1.0) -> None:
    """FastAPI-endpoint-friendly wrapper — raises HTTPException(429) with
    Retry-After, matching the plan's "standard 429 responses carrying
    Retry-After and X-RateLimit-* headers" (the X-RateLimit-* headers
    themselves need response-object access FastAPI dependencies don't
    have without a middleware; Retry-After is the load-bearing one for
    clients that actually back off, so it's what's implemented here)."""
    try:
        await check_rate_limit(bucket_key, capacity, refill_per_second, cost)
    except RateLimitExceeded as e:
        observability.RATE_LIMIT_REJECTIONS_TOTAL.labels(kind="run_agent").inc()
        raise HTTPException(
            status_code=429, detail="rate limit exceeded",
            headers={"Retry-After": str(int(e.retry_after_seconds) + 1)},
        )


# ── Credential-stuffing defense (plan §11.3): exponential backoff per
#    account and per IP on failed login attempts, not a flat rate limit —
#    a few mistyped passwords shouldn't lock someone out, but a sustained
#    guessing attempt against one account (or from one IP across many
#    accounts) should get slower with every failure, not stay constant.

_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 300.0     # 5 minutes — never lock out longer than this
_BACKOFF_WINDOW_SECONDS = 900    # failed-attempt counters decay after 15 min of no new failures
_BACKOFF_FREE_ATTEMPTS = 3       # this many failures cost nothing — a mistyped password
                                 # shouldn't lock anyone out; backoff is for SUSTAINED guessing


def _backoff_key(kind: str, identifier: str) -> str:
    return f"login_backoff:{kind}:{identifier}"


def _delay_for(failures: int) -> float:
    if failures <= _BACKOFF_FREE_ATTEMPTS:
        return 0.0
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (failures - _BACKOFF_FREE_ATTEMPTS)))


async def check_login_backoff(email: str, ip: str) -> None:
    """Raises HTTPException(429) if either the account or the IP is
    currently in a backoff window from prior failures. Call BEFORE
    verifying the password — a locked-out attempt shouldn't even run the
    (comparatively expensive) PBKDF2 check."""
    r = get_redis()
    for kind, identifier in (("email", email.lower()), ("ip", ip)):
        try:
            failures = await r.get(_backoff_key(kind, identifier))
        except Exception:
            continue  # fail open
        if failures is None:
            continue
        delay = _delay_for(int(failures))
        if delay <= 0:
            continue
        ttl = await r.ttl(_backoff_key(kind, identifier))
        # ttl counts down from _BACKOFF_WINDOW_SECONDS at the last failure;
        # only actually block while still inside this failure's own delay.
        elapsed_since_failure = _BACKOFF_WINDOW_SECONDS - max(0, ttl)
        if elapsed_since_failure < delay:
            observability.RATE_LIMIT_REJECTIONS_TOTAL.labels(kind="login").inc()
            raise HTTPException(
                status_code=429, detail="too many failed login attempts",
                headers={"Retry-After": str(int(delay - elapsed_since_failure) + 1)},
            )


async def record_login_failure(email: str, ip: str) -> None:
    r = get_redis()
    for kind, identifier in (("email", email.lower()), ("ip", ip)):
        try:
            key = _backoff_key(kind, identifier)
            await r.incr(key)
            await r.expire(key, _BACKOFF_WINDOW_SECONDS)
        except Exception:
            continue  # fail open


async def clear_login_failures(email: str, ip: str) -> None:
    r = get_redis()
    try:
        await r.delete(_backoff_key("email", email.lower()), _backoff_key("ip", ip))
    except Exception:
        pass
