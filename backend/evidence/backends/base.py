"""
evidence/backends/base.py — the pluggable evidence-publisher contract.

Mirrors notifications/backends/base.py and blockchain/signer.py exactly:
one narrow Protocol, one factory keyed off a config string, so swapping
disabled -> pinata is a config change, not a code change.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PublishResult:
    content_uri: str  # e.g. "ipfs://Qm..." — this IS anchorBatch()'s metaURI


class EvidencePublisher(Protocol):
    async def publish(self, manifest: dict) -> PublishResult: ...


class EvidencePublishError(Exception):
    """Raised by a backend for ANY reason evidence didn't get published —
    disabled, missing credentials, a real network/API failure. The caller
    (anchor_worker/main.py) catches this specifically and leaves metaURI
    empty / anchor_batches.evidence_cid NULL rather than fabricating a
    value — 'evidence unavailable' is always a legitimate, recorded
    outcome here, never silently papered over."""


def get_publisher(name: str) -> EvidencePublisher:
    if name == "disabled":
        from evidence.backends.disabled import DisabledPublisher
        return DisabledPublisher()
    if name == "pinata":
        from evidence.backends.pinata import PinataPublisher
        return PinataPublisher()
    raise ValueError(f"unknown evidence_publisher_backend: {name!r} (expected disabled|pinata)")
