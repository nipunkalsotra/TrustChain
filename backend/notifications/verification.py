"""
notifications/verification.py — sending the email-verification email.

Same "best-effort, inline, must never block or fail the action it
describes" discipline as notifications/invite.py (see that module's
docstring for the full reasoning): not routed through alert_deliveries
(nothing there to point a FK at — this isn't an alert), sent inline from
the request that triggered it. A failed send is not lost data — the
EmailVerificationToken row and its real token already exist either way —
and the caller has POST /auth/resend-verification to retry by hand.
"""

from config import get_settings
from logging_config import get_logger
from notifications.backends.base import EmailSendError, get_backend
from notifications.templates import render_verification_email

logger = get_logger(__name__)


async def queue_verification_email(*, user_id: int, email: str, name: str, raw_token: str, ttl_seconds: int) -> None:
    subject, text_body, html_body = render_verification_email(name=name, raw_token=raw_token, ttl_seconds=ttl_seconds)

    settings = get_settings()
    if not settings.alert_email_enabled:
        logger.info("verification_email_skipped_disabled", user_id=user_id, email=email)
        return

    backend = get_backend(settings.email_backend)
    try:
        await backend.send(to=email, subject=subject, text_body=text_body, html_body=html_body)
    except EmailSendError as e:
        logger.error("verification_email_failed", user_id=user_id, email=email, error=str(e))
