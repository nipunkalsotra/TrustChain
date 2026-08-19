"""notifications/backends/console.py — local-dev default. Renders the
email to the structured logger instead of sending anything, so a fresh
checkout needs zero email credentials to run (same "needs no external
account to work" bar as blockchain/signer.py's local backend)."""

import secrets

from logging_config import get_logger
from notifications.backends.base import SendResult

logger = get_logger(__name__)


class ConsoleBackend:
    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult:
        message_id = f"console-{secrets.token_hex(8)}"
        logger.info("email_console_send", to=to, subject=subject, message_id=message_id, body=text_body)
        return SendResult(provider_message_id=message_id)
