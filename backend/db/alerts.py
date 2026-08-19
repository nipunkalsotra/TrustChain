"""
db/alerts.py — raising, deduplicating, and reading integrity/security
alerts (Phase 3 §7, §8.1).

raise_alert() is the ONE function every detector (SDK drift check,
indexer identity-change handler, watchdog sweep, membership-mutation
endpoints) calls to surface a finding. It and its delivery fan-out commit
in a single transaction — the same transactional-outbox discipline
agents/base.py::log_step already applies to steps+anchor_outbox
(ADR-0001): a crash between "alert recorded" and "delivery queued" must
be impossible to observe from outside.
"""

import hashlib
import json
import time
from typing import Optional

from sqlalchemy import select, update

import observability
from db.engine import get_sessionmaker
from db.models import Alert, AlertDelivery, Membership, NotificationPreference, User

_SEVERITY_PREF_FIELD = {"critical": "email_critical", "warning": "email_warning", "info": "email_info"}


def compute_dedupe_key(alert_type: str, project_id: Optional[int], org_id: int, subject: str) -> str:
    """sha256(alert_type:scope:subject). Scope is project_id when present,
    else org_id — an org-level finding (e.g. ownership transfer) and a
    project-level one (e.g. a specific tampered step) must not collide
    just because they share a subject string."""
    scope = f"project:{project_id}" if project_id is not None else f"org:{org_id}"
    raw = f"{alert_type}:{scope}:{subject}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _recipients_for(session, org_id: int, severity: str) -> list[dict]:
    """Every owner/admin of org_id who wants an IMMEDIATE email for this
    severity (Phase 3 §7.3 — members/viewers can READ alerts through the
    API but are never emailed; a row-absent preference defaults to
    opted-in for critical/warning, opted-out for info, see
    NotificationPreference's docstring).

    email_digest_only=True recipients are deliberately EXCLUDED here for
    non-critical severities — they get folded into
    notifications/digest.py's periodic batch instead of an immediate
    alert_deliveries row (this is the fix for the gap ADR/session notes
    flagged: the preference existed and was settable via the API long
    before anything actually respected it). critical severity always
    goes out immediately regardless of digest_only — the whole point of
    a digest is to reduce noise from routine findings, not to delay
    "your audit trail no longer matches the chain" by up to a day."""
    pref_field = _SEVERITY_PREF_FIELD[severity]
    stmt = (
        select(User.id, User.email, User.email_verified, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, Membership.role.in_(("owner", "admin")))
    )
    rows = (await session.execute(stmt)).all()

    recipients = []
    for user_id, email, email_verified, role in rows:
        if not email_verified:
            # Phase 4 G1: an unverified address might not be this
            # person's — mailing it an alert (which can carry forensic
            # detail about who tampered with what) is exactly the kind
            # of consequential send verification exists to gate.
            continue
        pref = await session.get(NotificationPreference, {"user_id": user_id, "org_id": org_id})
        opted_in = getattr(pref, pref_field) if pref is not None else (severity != "info")
        wants_digest_only = pref is not None and pref.email_digest_only and severity != "critical"
        if opted_in and not wants_digest_only:
            recipients.append({"userId": user_id, "email": email, "role": role})
    return recipients


async def raise_alert(
    *,
    org_id: int,
    alert_type: str,
    severity: str,
    title: str,
    summary: str,
    subject: str,
    evidence: dict,
    project_id: Optional[int] = None,
    detector: Optional[str] = None,
    now: Optional[int] = None,
) -> dict:
    """Raises (or, if an open alert with the same dedupe_key already
    exists, bumps the occurrence count of) an alert, and queues one
    alert_deliveries row per eligible owner/admin recipient — all in one
    transaction. Returns {"alertId", "isNew", "occurrenceCount"}.

    Emailing on every occurrence would make a persistent problem
    unusable within hours (Phase 3 §7.2) — throttling which OCCURRENCES
    actually enqueue a delivery is notifications/sender.py's job (it
    checks alerts.last_emailed_at against alert_email_throttle_seconds
    before claiming), not this function's; raise_alert's job is only to
    make sure a delivery row EXISTS for every occurrence so the sender
    has something to throttle against.
    """
    now = now if now is not None else int(time.time())
    dedupe_key = compute_dedupe_key(alert_type, project_id, org_id, subject)
    evidence_json = json.dumps(evidence, sort_keys=True, default=str)

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        existing = (
            await session.execute(select(Alert).where(Alert.dedupe_key == dedupe_key, Alert.status == "open"))
        ).scalar_one_or_none()

        if existing is not None:
            existing.occurrence_count += 1
            existing.last_seen_at = now
            existing.evidence_json = evidence_json  # latest evidence supersedes stale
            await session.flush()
            alert_id, is_new, occurrence_count = existing.id, False, existing.occurrence_count
        else:
            alert = Alert(
                org_id=org_id, project_id=project_id, alert_type=alert_type, severity=severity, status="open",
                title=title, summary=summary, subject=subject, dedupe_key=dedupe_key, evidence_json=evidence_json,
                detector=detector, occurrence_count=1, first_seen_at=now, last_seen_at=now, created_at=now,
            )
            session.add(alert)
            await session.flush()
            alert_id, is_new, occurrence_count = alert.id, True, 1

            # Deliveries are only enqueued for a NEW alert here — a
            # recurring open alert's re-notification is a throttled
            # re-send of the SAME occurrence, handled by the sender
            # re-checking last_emailed_at, not a fresh delivery row per
            # occurrence (which would just race the throttle instead of
            # implementing it).
            for recipient in await _recipients_for(session, org_id, severity):
                session.add(AlertDelivery(
                    alert_id=alert_id, channel="email", recipient=recipient["email"], user_id=recipient["userId"],
                    status="pending", next_attempt_at=now, created_at=now,
                ))

        await session.commit()

    observability.INTEGRITY_ALERTS_RAISED_TOTAL.labels(alert_type=alert_type, severity=severity).inc()

    # Best-effort live-UI push (GET /alerts/stream) — after commit, never
    # inside the transaction above: this must not be able to roll back a
    # real alert over a Redis hiccup. See alert_events.py's module
    # docstring for why this is a separate, bounded stream rather than
    # reusing run_events.py's shape.
    from alert_events import publish_alert_event
    await publish_alert_event(org_id, {
        "id": alert_id, "orgId": org_id, "projectId": project_id, "alertType": alert_type, "severity": severity,
        "status": "open", "title": title, "summary": summary, "subject": subject,
        "occurrenceCount": occurrence_count, "isNew": is_new, "createdAt": now,
    })

    return {"alertId": alert_id, "isNew": is_new, "occurrenceCount": occurrence_count}


def _row_to_dict(a: Alert) -> dict:
    return {
        "id": a.id, "orgId": a.org_id, "projectId": a.project_id, "alertType": a.alert_type,
        "severity": a.severity, "status": a.status, "title": a.title, "summary": a.summary,
        "subject": a.subject, "detector": a.detector, "occurrenceCount": a.occurrence_count,
        "firstSeenAt": a.first_seen_at, "lastSeenAt": a.last_seen_at,
        "acknowledgedAt": a.acknowledged_at, "acknowledgedBy": a.acknowledged_by,
        "resolvedAt": a.resolved_at, "resolvedBy": a.resolved_by, "resolutionNote": a.resolution_note,
        # Phase 4 G4 — GET /alerts (the list endpoint the SDK's alerts()
        # wraps) previously omitted this entirely; only GET /alerts/{id}
        # included it (via a now-redundant second assignment in get_alert
        # below, since this function covers it too). Without it here, a
        # typed accessor on the SDK side would have nothing to read —
        # editedByOperator/oldOutputHash/etc. (integrity_watchdog/main.py's
        # _forensic_evidence) live inside this JSON blob. Small (a
        # handful of hash strings per alert), so including it in the list
        # response isn't a meaningful payload-size concern.
        "evidence": json.loads(a.evidence_json) if a.evidence_json else {},
    }


async def list_alerts(
    org_id: int, status: Optional[str] = None, severity: Optional[str] = None,
    alert_type: Optional[str] = None, project_id: Optional[int] = None,
    limit: int = 50, before_id: Optional[int] = None,
) -> tuple[list[dict], int]:
    """Cursor-paginated (before_id, newest-first by id) — GET /alerts.
    Returns (rows, total_open_count)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(Alert).where(Alert.org_id == org_id).order_by(Alert.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(Alert.status == status)
        if severity:
            stmt = stmt.where(Alert.severity == severity)
        if alert_type:
            stmt = stmt.where(Alert.alert_type == alert_type)
        if project_id is not None:
            stmt = stmt.where(Alert.project_id == project_id)
        if before_id is not None:
            stmt = stmt.where(Alert.id < before_id)

        rows = (await session.execute(stmt)).scalars().all()
        total_open = (await session.execute(
            select(Alert.id).where(Alert.org_id == org_id, Alert.status == "open")
        )).all()

    return [_row_to_dict(a) for a in rows], len(total_open)


async def get_alert(alert_id: int, org_id: int) -> Optional[dict]:
    """Scoped by org_id (invariant I7) — not just alert_id, which is a
    small sequential integer a caller could otherwise probe."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        alert = (
            await session.execute(select(Alert).where(Alert.id == alert_id, Alert.org_id == org_id))
        ).scalar_one_or_none()
        if alert is None:
            return None

        deliveries = (
            await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert_id))
        ).scalars().all()

    result = _row_to_dict(alert)  # already includes "evidence" — see that function
    result["deliveries"] = [
        {
            "channel": d.channel, "recipient": d.recipient, "status": d.status,
            "attempts": d.attempts, "sentAt": d.sent_at, "lastError": d.last_error,
        }
        for d in deliveries
    ]
    return result


async def alert_summary(org_id: int) -> dict:
    """GET /alerts/summary — one cheap query set for the nav badge."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(Alert.severity, Alert.status).where(Alert.org_id == org_id)
        rows = (await session.execute(stmt)).all()

    open_counts = {"critical": 0, "warning": 0, "info": 0}
    ack_counts = {"critical": 0, "warning": 0, "info": 0}
    total_open = 0
    for severity, status in rows:
        if status == "open":
            open_counts[severity] += 1
            total_open += 1
        elif status == "acknowledged":
            ack_counts[severity] += 1

    return {"open": open_counts, "acknowledged": ack_counts, "totalOpen": total_open}


async def acknowledge_alert(alert_id: int, org_id: int, user_id: int, now: int, note: Optional[str] = None) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Alert).where(Alert.id == alert_id, Alert.org_id == org_id, Alert.status == "open")
            .values(status="acknowledged", acknowledged_at=now, acknowledged_by=user_id)
        )
        await session.commit()
        return result.rowcount > 0


async def resolve_alert(alert_id: int, org_id: int, user_id: int, now: int, resolution_note: str) -> bool:
    """Freeing the alert's dedupe_key by setting status='resolved' is what
    lets a genuine recurrence raise a fresh open alert (Phase 3 §7.2's
    partial unique index only constrains status='open' rows)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Alert).where(Alert.id == alert_id, Alert.org_id == org_id, Alert.status != "resolved")
            .values(status="resolved", resolved_at=now, resolved_by=user_id, resolution_note=resolution_note)
        )
        await session.commit()
        return result.rowcount > 0


async def reopen_alert(alert_id: int, org_id: int, now: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Alert).where(Alert.id == alert_id, Alert.org_id == org_id, Alert.status == "resolved")
            .values(status="open", last_seen_at=now, resolved_at=None, resolved_by=None, resolution_note=None)
        )
        await session.commit()
        return result.rowcount > 0


# ── Notification preferences ────────────────────────────────────────────

_PREF_DEFAULTS = {"emailCritical": True, "emailWarning": True, "emailInfo": False, "emailDigestOnly": False}


async def get_notification_preferences(user_id: int, org_id: int) -> dict:
    """Absence of a row means defaults apply (NotificationPreference's
    docstring) — a brand-new member is opted into critical/warning email
    from the moment they join, no backfill needed."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        pref = await session.get(NotificationPreference, {"user_id": user_id, "org_id": org_id})
    if pref is None:
        return {"orgId": org_id, **_PREF_DEFAULTS}
    return {
        "orgId": org_id, "emailCritical": pref.email_critical, "emailWarning": pref.email_warning,
        "emailInfo": pref.email_info, "emailDigestOnly": pref.email_digest_only,
    }


async def set_notification_preferences(
    user_id: int, org_id: int, email_critical: bool, email_warning: bool,
    email_info: bool, email_digest_only: bool, now: int,
) -> None:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        pref = await session.get(NotificationPreference, {"user_id": user_id, "org_id": org_id})
        if pref is None:
            session.add(NotificationPreference(
                user_id=user_id, org_id=org_id, email_critical=email_critical, email_warning=email_warning,
                email_info=email_info, email_digest_only=email_digest_only, updated_at=now,
            ))
        else:
            pref.email_critical = email_critical
            pref.email_warning = email_warning
            pref.email_info = email_info
            pref.email_digest_only = email_digest_only
            pref.updated_at = now
        await session.commit()


# ── Digest (notifications/digest.py) ────────────────────────────────────

async def list_orgs_with_digest_subscribers() -> list[int]:
    """Orgs with at least one email_digest_only=True preference row —
    the outer loop notifications/digest.py iterates, so it never has to
    scan every org in the system on every check."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        rows = (await session.execute(
            select(NotificationPreference.org_id).where(NotificationPreference.email_digest_only.is_(True)).distinct()
        )).scalars().all()
        return list(rows)


async def get_due_digest_recipients(org_id: int, now: int, interval_seconds: int) -> list[dict]:
    """Every owner/admin in org_id with email_digest_only=True whose last
    digest (if any) was sent more than interval_seconds ago — a row-absent
    last_digest_sent_at (never sent) is always due."""
    stmt = (
        select(User.id, User.email, User.email_verified, NotificationPreference)
        .join(NotificationPreference, NotificationPreference.user_id == User.id)
        .join(Membership, (Membership.user_id == User.id) & (Membership.org_id == NotificationPreference.org_id))
        .where(
            NotificationPreference.org_id == org_id, NotificationPreference.email_digest_only.is_(True),
            Membership.role.in_(("owner", "admin")),
        )
    )
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()

    due = []
    for user_id, email, email_verified, pref in rows:
        if not email_verified:
            continue  # Phase 4 G1 — same reasoning as _recipients_for above
        last_sent = pref.last_digest_sent_at
        if last_sent is None or (now - last_sent) >= interval_seconds:
            due.append({"userId": user_id, "email": email})
    return due


async def list_alerts_for_digest(org_id: int, since: int) -> list[dict]:
    """Non-critical alerts (critical always goes out immediately, see
    _recipients_for) still open or acknowledged (not resolved — a digest
    is about live problems, not a changelog) that have been seen since
    the recipient's last digest."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        rows = (await session.execute(
            select(Alert).where(
                Alert.org_id == org_id, Alert.severity != "critical", Alert.status.in_(("open", "acknowledged")),
                Alert.last_seen_at >= since,
            ).order_by(Alert.severity, Alert.last_seen_at.desc())
        )).scalars().all()
    return [_row_to_dict(a) for a in rows]


async def mark_digest_sent(user_id: int, org_id: int, now: int) -> None:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(NotificationPreference)
            .where(NotificationPreference.user_id == user_id, NotificationPreference.org_id == org_id)
            .values(last_digest_sent_at=now)
        )
        await session.commit()
