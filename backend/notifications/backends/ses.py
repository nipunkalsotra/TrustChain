"""notifications/backends/ses.py — AWS SES, the production choice for the
EC2 deploy target (Phase 3 plan §7.4/§14): no SMTP credentials need to
live on the box — an IAM instance role attached to the EC2 instance is
enough — and deliverability is meaningfully better than raw SMTP for a
platform sending security-relevant alert mail.

boto3 clients are themselves synchronous, so (same reasoning as
notifications/backends/smtp.py) the actual call runs in a thread via
asyncio.to_thread rather than blocking the event loop.

REAL PREREQUISITE, not implied by this file existing: a verified sending
domain, SPF/DKIM/DMARC DNS records, and exiting the SES sandbox (which
otherwise only permits sending to addresses that are THEMSELVES verified)
are real AWS-account setup steps this code cannot do for you — see the
Phase 3 plan §16's rollout step 6 and its "deliverability is a real
prerequisite, not a detail" callout. Until that's done, `ses` will only
successfully deliver to sandbox-verified addresses.
"""

import asyncio

from config import get_settings
from notifications.backends.base import EmailSendError, SendResult


class SesBackend:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            settings = get_settings()
            self._client = boto3.client("sesv2", region_name=settings.ses_region or None)
        return self._client

    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult:
        settings = get_settings()
        try:
            response = await asyncio.to_thread(self._send_sync, to, subject, text_body, html_body, settings)
        except Exception as e:
            raise EmailSendError(str(e)) from e
        return SendResult(provider_message_id=response["MessageId"])

    def _send_sync(self, to: str, subject: str, text_body: str, html_body: str, settings) -> dict:
        client = self._get_client()
        kwargs = dict(
            FromEmailAddress=f"{settings.email_from_name} <{settings.email_from}>",
            Destination={"ToAddresses": [to]},
            Content={"Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            }},
        )
        if settings.ses_configuration_set:
            kwargs["ConfigurationSetName"] = settings.ses_configuration_set
        return client.send_email(**kwargs)
