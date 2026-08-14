"""
db/idempotency.py — replay protection for mutating endpoints (plan §8.4).

An `Idempotency-Key` header lets a client retry a request that timed out
or whose response was lost in transit without risking a duplicate side
effect (e.g. starting the same pipeline run twice, spending gas/LLM
credits twice for what the client believes was one call). The SDK
generates a UUIDv4 automatically for every mutating call; a human/web
caller opts in by supplying the header explicitly.

Same key + same request body -> the stored response is replayed, the
handler body never re-runs. Same key + a DIFFERENT body -> 409, since that
means the client is reusing a key for a genuinely different request,
which is a client bug worth surfacing loudly rather than silently doing
the wrong one of the two things it could mean.
"""

import hashlib
import json
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from db.engine import get_sessionmaker
from db.models import IdempotencyKey

_RETENTION_SECONDS = 24 * 3600  # plan §14.3: idempotency keys retained 24h


def _fingerprint(method: str, path: str, body: bytes) -> str:
    return hashlib.sha256(method.encode() + b"|" + path.encode() + b"|" + body).hexdigest()


class IdempotencyConflict(Exception):
    """Same key, different request — the caller (main.py) maps this to a 409."""


async def get_cached_response(project_id: int, key: str, method: str, path: str, body: bytes) -> Optional[dict]:
    """Returns the previously-stored {"statusCode", "body"} if this exact
    (project, key, request) was already handled — None if this key hasn't
    been seen yet FOR THIS PROJECT (caller should proceed and call
    store_response() after). Raises IdempotencyConflict if the SAME
    project reused this key for a different request — a different
    project reusing the same key string is a distinct, unrelated row
    (see IdempotencyKey's composite-PK docstring), not a conflict at all."""
    fingerprint = _fingerprint(method, path, body)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = await session.get(IdempotencyKey, (project_id, key))
        if row is None:
            return None
        if row.request_fingerprint != fingerprint:
            raise IdempotencyConflict(f"Idempotency-Key {key!r} was already used for a different request")
        return {"statusCode": row.status_code, "body": json.loads(row.response_json)}


async def store_response(
    project_id: int, key: str, method: str, path: str, body: bytes, status_code: int, response_body: dict, now: int
) -> None:
    fingerprint = _fingerprint(method, path, body)
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = pg_insert(IdempotencyKey).values(
            project_id=project_id, key=key, request_fingerprint=fingerprint,
            response_json=json.dumps(response_body), status_code=status_code, created_at=now,
        )
        # ON CONFLICT DO NOTHING, not DO UPDATE: the first response for a
        # (project, key) pair is authoritative — if two requests with the
        # same brand-new key raced past get_cached_response() before
        # either stored a result (should be rare — the client isn't
        # supposed to fire the same key concurrently — but not
        # impossible), whichever commits first wins and the second's
        # write is silently dropped rather than overwriting a response
        # some caller may already be reading.
        stmt = stmt.on_conflict_do_nothing(index_elements=[IdempotencyKey.project_id, IdempotencyKey.key])
        try:
            await session.execute(stmt)
            await session.commit()
        except IntegrityError:
            await session.rollback()
