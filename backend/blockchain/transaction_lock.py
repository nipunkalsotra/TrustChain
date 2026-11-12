"""Serialize nonce allocation through confirmation across processes sharing a signer."""

import asyncio
import hashlib
import inspect
from functools import wraps

from sqlalchemy import text

from db.engine import get_engine


def serialized_transaction(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def locked(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs).arguments
        signer = arguments.get("signer") or function.__globals__["get_signer"]()
        w3 = arguments.get("w3") or function.__globals__["get_w3"]()
        chain_id = await asyncio.to_thread(lambda: w3.eth.chain_id)
        identity = f"{chain_id}:{signer.address.lower()}".encode()
        key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        async with get_engine().begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            return await function(*args, **kwargs)

    return locked
