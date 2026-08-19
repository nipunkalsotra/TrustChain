"""
notifications/templates.py — alert & invitation email content (Phase 3
§7.5).

Plain f-string rendering, not a templating engine (Jinja2 etc.) — the
content here is simple enough (a handful of fixed layouts, no user-
authored templates, no need for template inheritance) that adding a new
dependency and an autoescaping-config surface to get right would cost
more than it buys. If that changes (a customizable-per-org template
becomes a real requirement), revisit then.

Rules every renderer here follows (plan §7.5):
  - NEVER include secrets (tokens, API keys) in alert mail — the one
    exception is the invitation email, where the link IS the payload.
  - ALWAYS include the raw evidence (hashes, tx, block, ids) so a
    recipient can independently verify the claim rather than trust this
    email's prose.
  - ALWAYS include a deep link into the frontend.
  - ALWAYS include a preferences link.
  - Both a text and an HTML part.
"""

import html
from typing import Optional

from config import get_settings


def _frontend_url(path: str) -> str:
    base = (get_settings().frontend_url or "https://app.trustchain.local").rstrip("/")
    return f"{base}{path}"


def _evidence_lines(evidence: dict) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in sorted(evidence.items()))


def _evidence_rows_html(evidence: dict) -> str:
    rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{html.escape(str(k))}</td>"
        f"<td style='padding:2px 0;font-family:monospace'>{html.escape(str(v))}</td></tr>"
        for k, v in sorted(evidence.items())
    )
    return f"<table style='font-size:13px'>{rows}</table>"


def render_alert_email(
    *, org_name: str, project_name: Optional[str], alert_id: int, alert_type: str, severity: str,
    title: str, summary: str, evidence: dict, occurrence_count: int, detected_at_iso: str,
) -> tuple[str, str, str]:
    """Returns (subject, text_body, html_body). Matches the Phase 3 plan
    §7.5 example verbatim in shape (severity tag, org/project, evidence
    block, deep link)."""
    scope = f"{org_name} / {project_name}" if project_name else org_name
    subject = f"[TrustChain {severity.upper()}] {title} — {scope}"
    recur = f" (occurrence #{occurrence_count})" if occurrence_count > 1 else ""
    link = _frontend_url(f"/alerts/{alert_id}")
    prefs_link = _frontend_url("/settings/notifications")

    text_body = (
        f"{summary}{recur}\n\n"
        f"  Organization   {org_name}\n"
        f"  Project        {project_name or '(org-level)'}\n"
        f"  Alert type     {alert_type}\n"
        f"  Detected       {detected_at_iso}\n\n"
        f"{_evidence_lines(evidence)}\n\n"
        f"  Review: {link}\n\n"
        f"  Manage notification preferences: {prefs_link}\n"
    )

    html_body = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<p>{html.escape(summary)}{html.escape(recur)}</p>"
        f"<p><b>Organization</b> {html.escape(org_name)}<br>"
        f"<b>Project</b> {html.escape(project_name or '(org-level)')}<br>"
        f"<b>Alert type</b> {html.escape(alert_type)}<br>"
        f"<b>Detected</b> {html.escape(detected_at_iso)}</p>"
        f"{_evidence_rows_html(evidence)}"
        f"<p><a href='{link}'>Review this alert</a></p>"
        f"<p style='color:#888;font-size:12px'><a href='{prefs_link}'>Manage notification preferences</a></p>"
        f"</div>"
    )
    return subject, text_body, html_body


def render_alert_digest_email(*, org_name: str, alerts: list[dict]) -> tuple[str, str, str]:
    subject = f"[TrustChain] Daily digest — {len(alerts)} warning alert(s) for {org_name}"
    prefs_link = _frontend_url("/settings/notifications")

    lines = "\n".join(f"  - [{a['severity']}] {a['title']} (x{a['occurrenceCount']})" for a in alerts)
    text_body = f"{len(alerts)} alert(s) for {org_name} in the last 24 hours:\n\n{lines}\n\n  Manage preferences: {prefs_link}\n"

    rows = "".join(
        f"<li>[{html.escape(a['severity'])}] {html.escape(a['title'])} (x{a['occurrenceCount']})</li>" for a in alerts
    )
    html_body = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<p>{len(alerts)} alert(s) for {html.escape(org_name)} in the last 24 hours:</p>"
        f"<ul>{rows}</ul>"
        f"<p style='color:#888;font-size:12px'><a href='{prefs_link}'>Manage notification preferences</a></p>"
        f"</div>"
    )
    return subject, text_body, html_body


def render_invitation_email(*, org_name: str, role: str, invited_by_name: str, raw_token: str) -> tuple[str, str, str]:
    """The one email in this module that DOES carry a secret — the
    invitation link itself is the payload (plan §7.5's stated exception)."""
    subject = f"{invited_by_name} invited you to join {org_name} on TrustChain"
    link = _frontend_url(f"/invite/{raw_token}")

    text_body = (
        f"{invited_by_name} has invited you to join \"{org_name}\" on TrustChain as {role}.\n\n"
        f"  Accept: {link}\n\n"
        f"This link is single-use and expires in 7 days. If you weren't expecting this, you can ignore it.\n"
    )
    html_body = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<p>{html.escape(invited_by_name)} has invited you to join "
        f"<b>{html.escape(org_name)}</b> on TrustChain as <b>{html.escape(role)}</b>.</p>"
        f"<p><a href='{link}' style='display:inline-block;padding:10px 20px;background:#0f4f4f;color:#fff;"
        f"text-decoration:none;border-radius:4px'>Accept invitation</a></p>"
        f"<p style='color:#888;font-size:12px'>This link is single-use and expires in 7 days. "
        f"If you weren't expecting this, you can ignore it.</p>"
        f"</div>"
    )
    return subject, text_body, html_body


def render_verification_email(*, name: str, raw_token: str, ttl_seconds: int) -> tuple[str, str, str]:
    """Same stated exception as the invitation email above: the token IS
    the payload. The raw token is surfaced as plain text too, not only
    inside the link — there is no frontend route to consume it yet
    (Phase 4 is backend-only; Phase 5 adds one), so `POST
    /auth/verify-email/{token}` is what a caller actually hits today,
    exactly as docs/e2e-walkthrough.md's Stage 1 does."""
    subject = "Verify your email address for TrustChain"
    link = _frontend_url(f"/verify-email/{raw_token}")
    hours = ttl_seconds // 3600

    text_body = (
        f"Hi {name},\n\n"
        f"Confirm this is your email address to finish setting up your TrustChain account.\n\n"
        f"  Verification token: {raw_token}\n"
        f"  Or click: {link}\n\n"
        f"This link/token is single-use and expires in {hours} hours. If you didn't create this "
        f"account, you can ignore this email — nothing further happens until it's verified.\n"
    )
    html_body = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<p>Hi {html.escape(name)},</p>"
        f"<p>Confirm this is your email address to finish setting up your TrustChain account.</p>"
        f"<p><a href='{link}' style='display:inline-block;padding:10px 20px;background:#0f4f4f;color:#fff;"
        f"text-decoration:none;border-radius:4px'>Verify email address</a></p>"
        f"<p style='color:#888;font-size:12px'>Verification token: <code>{html.escape(raw_token)}</code></p>"
        f"<p style='color:#888;font-size:12px'>This link/token is single-use and expires in {hours} hours. "
        f"If you didn't create this account, you can ignore this email.</p>"
        f"</div>"
    )
    return subject, text_body, html_body


def render_password_reset_email(*, name: str, raw_token: str, ttl_seconds: int) -> tuple[str, str, str]:
    """Deliberately does NOT reveal whether the account exists (main.py's
    /auth/forgot-password never calls this for an unknown email at all —
    see that endpoint's docstring), so by the time this renders, the
    caller already knows the account is real. The token is a live
    account-takeover credential if intercepted, same class of secret as
    the invitation/verification tokens above, surfaced the same way."""
    subject = "Reset your TrustChain password"
    link = _frontend_url(f"/reset-password/{raw_token}")
    minutes = ttl_seconds // 60

    text_body = (
        f"Hi {name},\n\n"
        f"Someone (hopefully you) requested a password reset for your TrustChain account.\n\n"
        f"  Reset token: {raw_token}\n"
        f"  Or click: {link}\n\n"
        f"This link/token is single-use and expires in {minutes} minutes. If you didn't request this, "
        f"you can ignore this email — your password will not change.\n"
    )
    html_body = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<p>Hi {html.escape(name)},</p>"
        f"<p>Someone (hopefully you) requested a password reset for your TrustChain account.</p>"
        f"<p><a href='{link}' style='display:inline-block;padding:10px 20px;background:#0f4f4f;color:#fff;"
        f"text-decoration:none;border-radius:4px'>Reset password</a></p>"
        f"<p style='color:#888;font-size:12px'>Reset token: <code>{html.escape(raw_token)}</code></p>"
        f"<p style='color:#888;font-size:12px'>This link/token is single-use and expires in {minutes} minutes. "
        f"If you didn't request this, you can ignore this email — your password will not change.</p>"
        f"</div>"
    )
    return subject, text_body, html_body
