"""
redis_client.py — lazy async Redis singleton.

Same discipline as config.get_settings()/db.engine.get_engine(): never
construct the connection at import time, only on first real use, so tests
can override REDIS_URL before anything touches it.
"""

from functools import lru_cache

import redis.asyncio as redis

from config import get_settings


@lru_cache
def get_redis() -> "redis.Redis":
    # socket_timeout=None — no client-side read timeout on top of Redis's
    # own semantics. run_events.read_events() issues XREAD with a `block`
    # argument up to 120s (SSE long-poll); a finite client-side
    # socket_timeout shorter than that (redis-py's own default) fires
    # BEFORE Redis's BLOCK naturally expires, raising redis.exceptions.
    # TimeoutError — which is NOT a subclass of the builtin TimeoutError,
    # so it slipped straight past main.py's `except TimeoutError:` and
    # silently killed the SSE stream mid-run with no error event ever
    # reaching the client. Caught as a real bug via the SDK's integration
    # tests hammering this path under concurrent load — see
    # run_events.py's read_events docstring for the other half of the
    # fix (mapping that exception type, as defense-in-depth regardless of
    # this setting). Fast commands (rate-limiting's Lua EVAL, XADD) are
    # unaffected — socket_timeout is a ceiling, not a target duration.
    return redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=None)
