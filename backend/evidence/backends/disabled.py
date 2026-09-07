"""evidence/backends/disabled.py — the default. Publishes nothing, ever.

Not a no-op success (that would mean silently claiming evidence
availability that doesn't exist) — raises EvidencePublishError every
time, same as notifications/backends/console.py logging-instead-of-
sending is a deliberate, distinct choice from "succeeding with nothing
to show for it." anchor_worker/main.py catches this exactly like a real
Pinata failure: metaURI stays empty, evidence_cid stays NULL, anchoring
itself proceeds unaffected."""

from evidence.backends.base import EvidencePublishError, PublishResult


class DisabledPublisher:
    async def publish(self, manifest: dict) -> PublishResult:
        raise EvidencePublishError("evidence publishing is disabled (evidence_publisher_backend=disabled)")
