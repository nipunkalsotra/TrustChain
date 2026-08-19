"""
notifications/sender.py — claims and sends pending alert_deliveries rows.

Same FOR UPDATE SKIP LOCKED outbox-claiming shape as
anchor_worker/claim.py: many sender loops could run concurrently (today
it runs as a task inside integrity_watchdog's process, but nothing here
assumes single-instance beyond that), and two must never send the same
delivery twice. A crash after claiming but before sending leaves a row
stuck at status='claimed' — same "something has to reap that" caveat
claim.py's own docstring makes; see run_forever's own claimed-row timeout
sweep below for the Phase 3 equivalent of anchor_worker/reaper.py.
"""

import asyncio
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import observability
from config import get_settings
from db.engine import get_sessionmaker
from db.models import Alert
from logging_config import get_logger
from notifications.backends.base import EmailSendError, get_backend
from notifications.templates import render_alert_email

logger = get_logger(__name__)

_CLAIM_TIMEOUT_SECONDS = 120  # a claimed-but-unsent row older than this is assumed to be from a dead sender


async def claim_deliveries(session: AsyncSession, worker_id: str, batch_size: int) -> list[dict]:
    now = int(time.time())
    result = await session.execute(
        text("""
            WITH claimed AS (
                SELECT id
                FROM alert_deliveries
                WHERE status = 'pending' AND next_attempt_at <= :now
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            )
            UPDATE alert_deliveries
            SET status = 'claimed', claimed_by = :worker_id, claimed_at = :now, attempts = attempts + 1
            FROM claimed
            WHERE alert_deliveries.id = claimed.id
            RETURNING alert_deliveries.id, alert_deliveries.alert_id, alert_deliveries.recipient,
                      alert_deliveries.channel, alert_deliveries.attempts
        """),
        {"now": now, "batch_size": batch_size, "worker_id": worker_id},
    )
    rows = [dict(r._mapping) for r in result]
    await session.commit()
    return rows


async def reap_stuck_claims(session: AsyncSession) -> int:
    """A sender that claims a row and then crashes leaves it stuck at
    'claimed' forever unless something resets it — same reasoning as
    anchor_worker/reaper.py for anchor_outbox."""
    cutoff = int(time.time()) - _CLAIM_TIMEOUT_SECONDS
    result = await session.execute(
        text("UPDATE alert_deliveries SET status = 'pending' WHERE status = 'claimed' AND claimed_at < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    return result.rowcount


# Explicit whitelist, not "whatever column names the caller happens to
# pass" — every call site below only ever uses a fixed, literal set of
# kwargs, but building UPDATE ... SET <interpolated column names> from an
# open **kwargs is exactly the risky SHAPE bandit's B608 flags regardless
# of what's actually reachable today; this closes it rather than relying
# on "every current call site happens to be safe" staying true forever.
_MARK_ALLOWED_COLUMNS = frozenset({
    "status", "next_attempt_at", "last_error", "sent_at", "provider_message_id",
})


async def _mark(session: AsyncSession, delivery_id: int, **values) -> None:
    unknown = set(values) - _MARK_ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"_mark: not in the allowed column set: {sorted(unknown)}")
    cols = ", ".join(f"{k} = :{k}" for k in values)
    # nosec B608 — bandit can't see the `unknown` check two lines above:
    # every `k` here is drawn from `values`, and `values`' keys were just
    # verified to be a subset of the fixed _MARK_ALLOWED_COLUMNS literal
    # set (raises ValueError otherwise). Never attacker- or caller-
    # controlled column names — the actual VALUES are still fully
    # parameter-bound (`:id`, **values), only column identifiers are
    # interpolated, and those are whitelisted, not user input.
    await session.execute(
        text(f"UPDATE alert_deliveries SET {cols} WHERE id = :id"),  # nosec B608
        {"id": delivery_id, **values},
    )
    await session.commit()


async def send_one(session: AsyncSession, delivery: dict) -> None:
    settings = get_settings()
    alert = await session.get(Alert, delivery["alert_id"])
    if alert is None:
        # The alert was deleted (shouldn't happen — alerts are never hard-
        # deleted — but fail safe rather than crash the loop over one bad row).
        await _mark(session, delivery["id"], status="failed", last_error="alert no longer exists")
        return

    import json
    evidence = json.loads(alert.evidence_json) if alert.evidence_json else {}
    from datetime import datetime, timezone
    detected_at_iso = datetime.fromtimestamp(alert.first_seen_at, tz=timezone.utc).isoformat()

    from db.models import Organization, Project
    org = await session.get(Organization, alert.org_id)
    project = await session.get(Project, alert.project_id) if alert.project_id else None

    subject, text_body, html_body = render_alert_email(
        org_name=org.name if org else f"org {alert.org_id}",
        project_name=project.name if project else None,
        alert_id=alert.id, alert_type=alert.alert_type, severity=alert.severity,
        title=alert.title, summary=alert.summary, evidence=evidence,
        occurrence_count=alert.occurrence_count, detected_at_iso=detected_at_iso,
    )

    backend = get_backend(settings.email_backend)
    start = time.monotonic()
    try:
        result = await backend.send(to=delivery["recipient"], subject=subject, text_body=text_body, html_body=html_body)
    except EmailSendError as e:
        attempts = delivery["attempts"]
        if attempts >= settings.alert_delivery_max_attempts:
            await _mark(session, delivery["id"], status="dead_letter", last_error=str(e))
            observability.ALERT_DELIVERIES_TOTAL.labels(channel=delivery["channel"], status="dead_letter").inc()
            logger.error("alert_delivery_dead_lettered", delivery_id=delivery["id"], error=str(e))
            # Failing to tell someone something important must not fail
            # silently — surface it as its own (org-less, so routed to
            # nobody by email — this is a platform-operator concern, not a
            # tenant one) log-visible event. A real deployment should
            # alert on this log line directly (structured, grep-able)
            # rather than trying to loop delivery-failure through the
            # SAME alerting pipeline that just failed to deliver.
        else:
            backoff = min(
                settings.alert_delivery_backoff_max_seconds,
                settings.alert_delivery_backoff_base_seconds * (2 ** (attempts - 1)),
            )
            await _mark(
                session, delivery["id"], status="pending",
                next_attempt_at=int(time.time() + backoff), last_error=str(e),
            )
            observability.ALERT_DELIVERIES_TOTAL.labels(channel=delivery["channel"], status="retry").inc()
        return

    now = int(time.time())
    await _mark(session, delivery["id"], status="sent", sent_at=now, provider_message_id=result.provider_message_id)
    await session.execute(text("UPDATE alerts SET last_emailed_at = :now WHERE id = :id"), {"now": now, "id": alert.id})
    await session.commit()
    observability.ALERT_DELIVERIES_TOTAL.labels(channel=delivery["channel"], status="sent").inc()
    observability.ALERT_DELIVERY_LATENCY_SECONDS.labels(channel=delivery["channel"]).observe(time.monotonic() - start)


async def run_once(worker_id: str, batch_size: int = 50) -> int:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await reap_stuck_claims(session)
        deliveries = await claim_deliveries(session, worker_id, batch_size)

    async with session_factory() as session:
        for delivery in deliveries:
            await send_one(session, delivery)

    depth_session_factory = get_sessionmaker()
    async with depth_session_factory() as session:
        result = await session.execute(text("SELECT count(*) FROM alert_deliveries WHERE status IN ('pending','claimed')"))
        observability.ALERT_DELIVERY_QUEUE_DEPTH.set(result.scalar_one())

    return len(deliveries)


async def run_forever(worker_id: str, poll_interval_seconds: float = 5.0) -> None:
    logger.info("notification_sender_starting", worker_id=worker_id)
    while True:
        try:
            handled = await run_once(worker_id)
        except Exception as e:
            logger.error("notification_sender_iteration_failed", error=str(e))
            handled = 0
        await asyncio.sleep(poll_interval_seconds if handled == 0 else 0.1)
