"""
notifications/backends/base.py — the pluggable email-backend contract.

Mirrors blockchain/signer.py's pluggable local/KMS/Vault signing backends
(ADR-0008) exactly: one narrow Protocol, one factory keyed off a config
string, so swapping console -> smtp -> ses is a config change, not a code
change, and there's exactly one place ("what does 'sending email' mean
right now") a new backend has to plug into.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SendResult:
    provider_message_id: str


class EmailBackend(Protocol):
    async def send(self, *, to: str, subject: str, text_body: str, html_body: str) -> SendResult: ...


class EmailSendError(Exception):
    """Raised by a backend on any delivery failure — notifications/sender.py
    catches this specifically (not a bare Exception) so a backend that
    raises something else (a programming bug) still surfaces as a real
    crash instead of being silently treated as 'retry later'."""


def get_backend(name: str) -> EmailBackend:
    if name == "console":
        from notifications.backends.console import ConsoleBackend
        return ConsoleBackend()
    if name == "smtp":
        from notifications.backends.smtp import SmtpBackend
        return SmtpBackend()
    if name == "ses":
        from notifications.backends.ses import SesBackend
        return SesBackend()
    if name == "brevo":
        from notifications.backends.brevo import BrevoBackend
        return BrevoBackend()
    if name == "memory":
        from notifications.backends.memory import MemoryBackend
        return MemoryBackend()
    raise ValueError(f"unknown email_backend: {name!r} (expected console|smtp|ses|brevo|memory)")
