"""evidence/backends/pinata.py — publishes a batch's manifest to IPFS via
Pinata's pinning REST API (https://docs.pinata.cloud/api-reference/endpoint/pin-json-to-ipfs).

Chosen over a raw go-ipfs/Arweave node for the same reason
notifications/backends/brevo.py chose a REST API over raw SMTP: one HTTPS
POST, one JWT, no infrastructure this project would have to run and keep
available itself. Real production-ready adapter, not a stub — the only
thing untested in THIS repo's own CI is a live Pinata account's
credentials (no such account exists for this deployment, same honest
limitation as V1's real Monad testnet deployer key)."""

import httpx

from config import get_settings
from evidence.backends.base import EvidencePublishError, PublishResult


class PinataPublisher:
    async def publish(self, manifest: dict) -> PublishResult:
        settings = get_settings()
        if not settings.pinata_jwt:
            raise EvidencePublishError("evidence_publisher_backend=pinata but pinata_jwt is not configured")

        # pinataContent takes the manifest object directly — Pinata does
        # its own JSON encoding on their side.
        payload = {
            "pinataContent": manifest,
            "pinataMetadata": {"name": f"trustchain-batch-{manifest.get('runIdHash', 'unknown')}"},
            "pinataOptions": {"cidVersion": 1},
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    settings.pinata_api_url,
                    headers={"Authorization": f"Bearer {settings.pinata_jwt}"},
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise EvidencePublishError(str(e)) from e

        if response.status_code >= 300:
            # Pinata's error body is JSON ({"error": {"reason": ..., "details": ...}})
            # — surfaced verbatim, same reasoning as brevo.py's identical choice:
            # a real rejection (bad JWT, plan limit, suspended account) should
            # show up as-is in anchor_batches' logs, not a generic status code.
            raise EvidencePublishError(f"pinata {response.status_code}: {response.text}")

        body = response.json()
        cid = body.get("IpfsHash")
        if not cid:
            raise EvidencePublishError(f"pinata response missing IpfsHash: {body}")

        return PublishResult(content_uri=f"ipfs://{cid}")
