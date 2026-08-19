"""
alert_events.py — Redis Streams-backed SSE event bus for GET /alerts/stream
(Phase 3 §9.6, listed "optional but cheap" in the plan and skipped in the
first pass — closed here).

Deliberately NOT the same shape as run_events.py in two ways, both
because an org's alert stream is long-lived (a run's stream is bounded
by the run's own lifetime and gets a TTL the moment it terminates):

  1. Bounded, not unbounded: `XADD ... MAXLEN ~` caps the stream so
     Redis memory doesn't grow forever for an org that's been alerting
     for months. GET /alerts (Postgres, the real system of record) is
     still where a client gets full history/current state on load; this
     stream is for LIVE deltas only.
  2. New connections start from "$" (only entries added from now on),
     not "0" (full replay) — replaying potentially thousands of
     historical alerts on every dashboard page load would be exactly the
     unbounded-cost mistake Phase 2's read-path work (event-sourced
     indexer) existed to eliminate, just relocated to a different layer.
"""

import json
from typing import AsyncGenerator, Optional

import redis.exceptions

from redis_client import get_redis

_STREAM_MAXLEN = 500
_BLOCK_MS = 25_000  # bounded, not infinite — see read_alert_events


def _stream_key(org_id: int) -> str:
    return f"alert_events:{org_id}"


async def publish_alert_event(org_id: int, alert: dict) -> None:
    """Called by db/alerts.py::raise_alert after its transaction commits
    — best-effort (an unreachable Redis must never fail or roll back the
    actual alert-raising transaction; the alert row and its email
    delivery are the durable guarantee, this is a live-UI convenience on
    top)."""
    try:
        r = get_redis()
        await r.xadd(_stream_key(org_id), {"data": json.dumps(alert, default=str)}, maxlen=_STREAM_MAXLEN, approximate=True)
    except Exception:
        pass


async def read_alert_events(org_id: int) -> AsyncGenerator[Optional[dict], None]:
    """Yields each new alert event for org_id as it's published, blocking
    between them. Yields None on an idle timeout (the caller sends an SSE
    keep-alive comment for it) rather than raising — unlike a pipeline
    run's stream, an org's alert stream has no natural end; the caller
    (main.py) relies on the client disconnecting (asyncio.CancelledError)
    to actually stop this generator, not a terminal marker."""
    r = get_redis()
    key = _stream_key(org_id)
    last_id = "$"  # only entries from now on — see module docstring

    while True:
        try:
            response = await r.xread({key: last_id}, block=_BLOCK_MS, count=20)
        except redis.exceptions.TimeoutError:
            yield None
            continue
        if not response:
            yield None
            continue

        _, entries = response[0]
        for entry_id, fields in entries:
            last_id = entry_id
            yield json.loads(fields["data"])
