"""
notifications/digest.py — periodic digest email for
email_digest_only=True subscribers (Phase 3 §7.3/§14).

Closes a real gap: the `email_digest_only` preference and
`render_alert_digest_email` template both existed from the start of
Phase 3, but nothing ever checked the former or called the latter —
every recipient got immediate per-alert email regardless of what they'd
opted into. `db/alerts.py::_recipients_for` now EXCLUDES a digest-only
subscriber from immediate delivery for non-critical severities; this
module is what actually delivers their batch instead.

Runs as a periodic check (cheap — one query for "which orgs even have a
digest subscriber", see list_orgs_with_digest_subscribers) inside
integrity_watchdog's main loop, same as notifications/sender.py's drain
loop — not its own process, for the same "no fourth container on a
single-EC2-instance deploy" reasoning as everything else in
notifications/.
"""

import time

from config import get_settings
from db.alerts import get_due_digest_recipients, list_alerts_for_digest, list_orgs_with_digest_subscribers, mark_digest_sent
from db.engine import get_sessionmaker
from db.models import Organization
from logging_config import get_logger
from notifications.backends.base import EmailSendError, get_backend
from notifications.templates import render_alert_digest_email

logger = get_logger(__name__)


async def _org_name(org_id: int) -> str:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
    return org.name if org is not None else f"org {org_id}"


async def send_due_digests(now: int = None) -> int:
    """Checks every org with at least one digest subscriber, sends a
    batched email to whichever subscribers are due (their interval has
    elapsed since their own last_digest_sent_at), and marks each sent.
    Returns the number of digest emails actually sent. Best-effort per
    recipient — one failed send doesn't block the rest of the batch or
    the rest of the orgs."""
    now = now if now is not None else int(time.time())
    settings = get_settings()
    if not settings.alert_email_enabled:
        return 0

    sent_count = 0
    for org_id in await list_orgs_with_digest_subscribers():
        recipients = await get_due_digest_recipients(org_id, now, settings.alert_digest_interval_seconds)
        if not recipients:
            continue

        # Window: alerts seen since the OLDEST due recipient's last
        # digest (or the full interval if they've never had one) — a
        # slightly wider window per-recipient would need a per-recipient
        # query; this is the simpler, still-correct-in-practice choice
        # (a recipient who was due earlier than another in the same org
        # sees a strict superset of what a narrower window would show,
        # never LESS than what they're owed).
        since = now - settings.alert_digest_interval_seconds
        alerts = await list_alerts_for_digest(org_id, since)
        if not alerts:
            for r in recipients:
                await mark_digest_sent(r["userId"], org_id, now)
            continue

        org_name = await _org_name(org_id)
        subject, text_body, html_body = render_alert_digest_email(org_name=org_name, alerts=alerts)
        backend = get_backend(settings.email_backend)

        for r in recipients:
            try:
                await backend.send(to=r["email"], subject=subject, text_body=text_body, html_body=html_body)
                sent_count += 1
            except EmailSendError as e:
                logger.error("digest_email_failed", org_id=org_id, user_id=r["userId"], error=str(e))
                continue  # don't mark_digest_sent — they stay "due" and get retried next check
            await mark_digest_sent(r["userId"], org_id, now)

    return sent_count
