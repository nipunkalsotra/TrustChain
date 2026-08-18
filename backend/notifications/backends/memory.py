"""notifications/backends/memory.py — test-only backend that captures
sent messages in a module-level list instead of sending anything real.
Module-level (not instance-level) so a test can import SENT directly
without needing a handle on whichever backend instance the code under
test constructed."""

import secrets

from notifications.backends.base import SendResult

SENT: list[dict] = []


class MemoryBackend:
    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult:
        message_id = f"memory-{secrets.token_hex(8)}"
        SENT.append({"to": to, "subject": subject, "textBody": text_body, "htmlBody": html_body, "messageId": message_id})
        return SendResult(provider_message_id=message_id)


def reset() -> None:
    SENT.clear()
