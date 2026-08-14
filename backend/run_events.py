"""
run_events.py — Redis Streams-backed SSE event bus for pipeline runs.

Replaces the in-process `_run_queues: dict[str, asyncio.Queue]` main.py
used through Phase 2.0-2.2 (F5 in the Phase 2 plan's fix list): a second
API replica has its own process memory, so a client connecting to
GET /stream/{run_id} on a DIFFERENT replica than the one running the
pipeline would see nothing at all under the old design — SSE silently
broke the moment there was more than one process. Redis Streams fix this
two ways at once:
  - durability: XADD persists each event; a client that connects a moment
    late (network jitter, browser startup) still sees everything from the
    beginning via XREAD starting at '0', not just events published after
    it subscribed — a plain Redis Pub/Sub channel would lose those, since
    Pub/Sub has no replay.
  - liveness: XREAD with BLOCK tails new entries in real time once caught up.

Each run's stream is a short-lived key (TTL set once the terminal marker
is published) — this is a live-event bus, not permanent storage; the
read model (Postgres, via the outbox/anchor worker/indexer) is the
durable audit trail.
"""

import json
from typing import AsyncGenerator

import redis.exceptions

from redis_client import get_redis

# How long a finished run's stream sticks around for a late/reconnecting
# reader before Redis reclaims it — generous relative to how long an SSE
# client would plausibly still be trying to catch up.
_STREAM_TTL_SECONDS = 300

_TERMINAL_MARKER = {"__terminal__": True}


def _stream_key(run_id: str) -> str:
    return f"run_events:{run_id}"


async def publish_event(run_id: str, event: dict) -> None:
    r = get_redis()
    await r.xadd(_stream_key(run_id), {"data": json.dumps(event)})


async def publish_terminal(run_id: str) -> None:
    """Marks the stream complete and schedules its cleanup. A dedicated
    terminal marker (rather than inferring completion from event
    contents, e.g. type == "run_complete") keeps read_events()'s exit
    condition simple and independent of whatever shape the pipeline's
    events happen to have — an "error" event ends the stream exactly the
    same way a "run_complete" one does."""
    r = get_redis()
    key = _stream_key(run_id)
    await r.xadd(key, {"data": json.dumps(_TERMINAL_MARKER)})
    await r.expire(key, _STREAM_TTL_SECONDS)


async def read_events(run_id: str, timeout_seconds: int = 120) -> AsyncGenerator[dict, None]:
    """Yields every event published for `run_id`, starting from the
    beginning of its stream (so a client connecting after the pipeline
    already started still sees run_started etc. — the same race the old
    in-process design handled by polling for the queue to appear), then
    blocks for new ones until the terminal marker or `timeout_seconds` of
    silence. Raises the builtin TimeoutError on silence — the caller
    (main.py) maps that to the same SSE error event the old design sent
    on queue.get() timeout. This covers TWO distinct timeout paths as the
    same exception: Redis's own BLOCK expiring with no new entries (the
    `not response` branch below) AND the redis-py client's own socket
    read timing out first, if it's shorter than `block` (redis.exceptions
    .TimeoutError — NOT a subclass of the builtin TimeoutError, confirmed
    via its __mro__, so it would otherwise slip straight past main.py's
    `except TimeoutError:` uncaught, silently killing the SSE stream
    mid-run with no error event ever reaching the client — a real bug
    this module's own test suite caught via a real Redis instance under
    concurrent load, not a hypothetical)."""
    r = get_redis()
    key = _stream_key(run_id)
    last_id = "0"

    while True:
        try:
            response = await r.xread({key: last_id}, block=timeout_seconds * 1000, count=50)
        except redis.exceptions.TimeoutError as e:
            raise TimeoutError(f"redis read timed out waiting for run {run_id!r}: {e}") from e
        if not response:
            raise TimeoutError(f"no events for run {run_id!r} within {timeout_seconds}s")

        _, entries = response[0]
        for entry_id, fields in entries:
            last_id = entry_id
            event = json.loads(fields["data"])
            if event.get("__terminal__"):
                return
            yield event
