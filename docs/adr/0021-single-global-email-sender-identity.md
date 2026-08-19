# 0021 — Single global email sender identity

**Status:** Accepted

## Context

Every outbound email TrustChain sends — alert notifications
(`notifications/sender.py`), invitations (`notifications/invite.py`),
email verification and password reset (Phase 4 G1/G2,
`notifications/verification.py`/`notifications/password_reset.py`) —
goes out from one process-wide `config.email_from`/`email_from_name`
pair, read once at startup, the same for every org and every email kind.
There is no per-org "reply-to your own support address" or per-kind
"verification emails come from a different address than alerts" concept
anywhere in the code.

Phase 4's plan flagged this explicitly as a gap worth a conscious
decision rather than an unreviewed omission (G6) — this ADR is that
decision.

## Decision

Keep exactly one sender identity, configured once
(`EMAIL_FROM`/`EMAIL_FROM_NAME`), for every org and every email kind,
for now.

This is deliberately the simplest thing that could work, not an
oversight:

- **Every current email backend requires the sender to be pre-verified
  with the provider** (`notifications/backends/brevo.py`'s docstring:
  "Brevo rejects unverified senders outright"; SES has the same
  requirement). Per-org sender addresses would mean either verifying
  every tenant's own domain with the email provider before their first
  invitation can send — a real onboarding step this product doesn't ask
  for anywhere else — or maintaining TrustChain's own pool of
  provider-verified addresses and picking one per org, which buys
  nothing a tenant would actually notice (the FROM name is still
  "TrustChain", not their own brand) at real implementation cost.
- **Nothing downstream depends on it being per-org.** `notifications/
  templates.py`'s renderers already put the org's name prominently in
  the subject/body of every email kind (e.g. `render_alert_email`'s `[TrustChain
  {severity}] {title} — {org_name}`) — a recipient can already tell
  which of their orgs an email is about without the FROM address
  carrying that information.
- **A real deployment operator can still change it** — `EMAIL_FROM`/
  `EMAIL_FROM_NAME` are ordinary settings, no code change needed to
  point them at a different verified identity. What's NOT supported is
  different senders for different orgs or different email kinds within
  one running deployment.

## Alternatives considered

- **Per-org sender address**, stored on `Organization` and read by each
  `render_*_email` call. Rejected for now — see the provider-verification
  cost above. Worth revisiting if TrustChain ever needs genuine
  white-label sending on behalf of tenants, which nothing in the current
  product surface asks for.
- **Per-email-kind sender** (e.g. `alerts@`/`noreply@` for verification
  vs `security@` for tamper alerts) — cosmetic, no functional benefit,
  and doubles the number of provider-verified identities every
  deployment has to set up before its first email can send. Rejected as
  complexity with no corresponding gap it closes.

## Consequences

- Every deployment needs exactly one provider-verified sender identity
  to get email working at all — simpler to operate than any per-org or
  per-kind scheme, at the cost of every tenant's email looking like it
  came from the same place (which, for a security/audit product where
  the recipient already trusts TrustChain as the sender of record, is
  arguably the more honest framing anyway — an alert email claiming to
  be from the tenant's own domain would be a *harder* message to trust,
  not an easier one).
- If a genuine white-label requirement shows up later, it's an additive
  change (a new nullable `Organization.email_from` column, falling back
  to the global default when unset) — not a rearchitecture, since every
  `render_*_email`/`queue_*_email` call site already receives the
  relevant org context and could be threaded a per-org override without
  touching call sites that don't need one.
