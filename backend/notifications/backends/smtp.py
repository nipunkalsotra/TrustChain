"""notifications/backends/smtp.py — generic SMTP+STARTTLS, stdlib only
(no aiosmtplib dependency — smtplib is synchronous, so the actual send
runs in a thread via asyncio.to_thread rather than blocking the event
loop notifications/sender.py runs on). Works with a Gmail account + app
password for early testing, per the Phase 3 plan's deliverability note —
production on the EC2 target should use the ses backend instead.

If outbound port 587 is unreliable in whatever environment this runs in
(seen for real in a sandboxed dev environment: intermittent DNS failures
and connect timeouts to a provider whose server was actually up, while
port 443 traffic was solid throughout) and the provider offers an HTTPS
transactional-email REST API instead, that's a real, working alternative
— see notifications/backends/brevo.py for the pattern."""

import asyncio
import smtplib
from email.message import EmailMessage

from config import get_settings
from notifications.backends.base import EmailSendError, SendResult


class SmtpBackend:
    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult:
        settings = get_settings()
        if not settings.smtp_host:
            raise EmailSendError("email_backend=smtp but smtp_host is not configured")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{settings.email_from_name} <{settings.email_from}>"
        msg["To"] = to
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        try:
            await asyncio.to_thread(self._send_sync, msg, settings)
        except Exception as e:
            raise EmailSendError(str(e)) from e

        # smtplib gives no provider message id — synthesize one from the
        # envelope so alert_deliveries.provider_message_id is at least
        # locally unique and grep-able in logs, same shape other backends
        # return even though there's nothing to correlate it against later.
        import hashlib
        digest = hashlib.sha256(f"{to}:{subject}:{text_body}".encode()).hexdigest()[:16]
        return SendResult(provider_message_id=f"smtp-{digest}")

    @staticmethod
    def _send_sync(msg: EmailMessage, settings) -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
            if settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(msg)
