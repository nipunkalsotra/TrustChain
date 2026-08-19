"""notifications/backends/brevo.py — Brevo's transactional email REST API
(POST https://api.brevo.com/v3/smtpEmail), reached over HTTPS rather than
their SMTP relay.

Exists specifically because Brevo issues two DIFFERENT credential types —
an `xsmtpsib-...` SMTP password and an unrelated `xkeysib-...` REST API
key — and, separately, because a real sandboxed dev environment this
stack was run in had genuinely flaky outbound port 587 (intermittent DNS
failures and connect timeouts even while the target server was reachable
and healthy), while port 443 traffic was solid throughout the same
session. A network that tolerates HTTPS but not raw SMTP is common enough
in locked-down environments that this backend is worth having as a
same-free-tier (300 emails/day) way around it, not just a smtp.py
duplicate — see notifications/backends/smtp.py's own updated comment.

httpx is already an async HTTP client (unlike smtplib/boto3, both
synchronous and run via asyncio.to_thread by their own backends), so this
is the one backend in this package that can await the network call
directly.
"""

import httpx

from config import get_settings
from notifications.backends.base import EmailSendError, SendResult


class BrevoBackend:
    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult:
        settings = get_settings()
        if not settings.brevo_api_key:
            raise EmailSendError("email_backend=brevo but brevo_api_key is not configured")

        payload = {
            "sender": {"name": settings.email_from_name, "email": settings.email_from},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    settings.brevo_api_url,
                    headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise EmailSendError(str(e)) from e

        if response.status_code >= 300:
            # Brevo's error body is JSON ({"code": ..., "message": ...}) —
            # surfaced verbatim so a real rejection (bad sender identity,
            # suspended account, rate limit) shows up as-is in
            # alert_deliveries.last_error instead of a generic status code.
            raise EmailSendError(f"brevo {response.status_code}: {response.text}")

        message_id = response.json().get("messageId", "")
        return SendResult(provider_message_id=message_id)
