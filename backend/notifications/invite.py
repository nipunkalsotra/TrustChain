"""
notifications/invite.py — sending the invitation email.

Deliberately NOT routed through alert_deliveries (that table is FK'd to
alerts.id — an invitation isn't an alert, there's nothing to point it
at). Sent inline, best-effort, from the request that created the
invitation — same "best-effort, must never block or fail the action it
describes" discipline main.py's audit_log_admin_action already applies to
platform audit logging. This is a deliberately smaller durability
guarantee than alert email gets: an invitation that fails to send is not
silently lost data (the Invitation row + its real token already exist
either way), and the admin has POST /orgs/{id}/invitations/{id}/resend to
retry by hand — a much cheaper mechanism than building outbox+retry
machinery for a doesn't-repeat, one-shot email.
"""

from config import get_settings
from logging_config import get_logger
from notifications.backends.base import EmailSendError, get_backend
from notifications.templates import render_invitation_email

logger = get_logger(__name__)


async def queue_invitation_email(
    *, org_id: int, email: str, role: str, raw_token: str, invited_by_name: str, now: int,
) -> None:
    from db.engine import get_sessionmaker
    from db.models import Organization

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
    org_name = org.name if org else f"org {org_id}"

    subject, text_body, html_body = render_invitation_email(
        org_name=org_name, role=role, invited_by_name=invited_by_name, raw_token=raw_token,
    )

    settings = get_settings()
    if not settings.alert_email_enabled:
        logger.info("invitation_email_skipped_disabled", org_id=org_id, email=email)
        return

    backend = get_backend(settings.email_backend)
    try:
        await backend.send(to=email, subject=subject, text_body=text_body, html_body=html_body)
    except EmailSendError as e:
        # Best-effort — see module docstring. The invitation itself still
        # exists and is still acceptable via its real link; this only
        # means the recipient didn't get emailed it automatically.
        logger.error("invitation_email_failed", org_id=org_id, email=email, error=str(e))
