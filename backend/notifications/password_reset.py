"""
notifications/password_reset.py — sending the password-reset email.

Same best-effort, inline discipline as notifications/verification.py/
notifications/invite.py. A failed send here is more consequential than
those two (there's no "resend" endpoint for this one — the caller just
calls POST /auth/forgot-password again, which mints a fresh token), but
the durability tradeoff is identical: building outbox+retry machinery for
a doesn't-repeat, rare, one-shot email would cost more than it buys.
"""

from config import get_settings
from logging_config import get_logger
from notifications.backends.base import EmailSendError, get_backend
from notifications.templates import render_password_reset_email

logger = get_logger(__name__)


async def queue_password_reset_email(*, user_id: int, email: str, name: str, raw_token: str, ttl_seconds: int) -> None:
    subject, text_body, html_body = render_password_reset_email(name=name, raw_token=raw_token, ttl_seconds=ttl_seconds)

    settings = get_settings()
    if not settings.alert_email_enabled:
        logger.info("password_reset_email_skipped_disabled", user_id=user_id, email=email)
        return

    backend = get_backend(settings.email_backend)
    try:
        await backend.send(to=email, subject=subject, text_body=text_body, html_body=html_body)
    except EmailSendError as e:
        logger.error("password_reset_email_failed", user_id=user_id, email=email, error=str(e))
