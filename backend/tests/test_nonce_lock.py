"""
tests/test_nonce_lock.py — F6 ("sole nonce authority", enforced for real
via anchor_worker/nonce_lock.py's Postgres advisory lock), against a real
Postgres — this is exactly the kind of session-scoped, cross-connection
behavior a mock can't meaningfully fake.
"""

import asyncio

from anchor_worker.nonce_lock import NonceAuthorityLock


def run(coro):
    return asyncio.run(coro)


def test_second_instance_cannot_acquire_while_first_holds_it():
    async def _scenario():
        first = NonceAuthorityLock()
        second = NonceAuthorityLock()
        try:
            assert await first.try_acquire() is True
            # A second, genuinely separate lock object (its own dedicated
            # connection, exactly like a second anchor-worker process
            # would have) must NOT be able to acquire the same lock while
            # the first is held.
            assert await second.try_acquire() is False
        finally:
            await first.release()
            await second.release()

    run(_scenario())


def test_second_instance_can_acquire_after_first_releases():
    async def _scenario():
        first = NonceAuthorityLock()
        second = NonceAuthorityLock()
        try:
            assert await first.try_acquire() is True
            await first.release()
            # Now genuinely free — a fresh instance must be able to take
            # over, matching the real "old worker drained, new one takes
            # nonce authority" handoff.
            assert await second.try_acquire() is True
        finally:
            await first.release()
            await second.release()

    run(_scenario())


def test_release_without_acquire_is_a_safe_noop():
    run(NonceAuthorityLock().release())
