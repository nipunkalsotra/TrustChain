# 0023 — Unverified-account permission limits

**Status:** Accepted

## Context

Email verification (ADR-0022) only matters if something depends on it —
otherwise it's a checkbox nobody's blocked by. Phase 4's plan named the
question directly: which actions should an unverified account be
blocked from, and where should that rule live so a future endpoint can't
silently skip it?

## Decision

An unverified user may log in and read freely. They may **not**:

1. **Invite a member** (`Permission.MEMBER_INVITE`) — an invitation is
   itself a real credential-granting email sent on the org's behalf;
   sending it from an account whose own address might not be genuinely
   theirs extends that same uncertainty to everyone they invite.
2. **Create an API key** (`Permission.APIKEY_CREATE`) — a live,
   long-lived machine credential minted by an account nobody's confirmed
   controls its own inbox.
3. **Receive alert emails** — `db/alerts.py`'s `_recipients_for` (the
   immediate-delivery path) and `get_due_digest_recipients` (the digest
   path) both filter out `email_verified=false` recipients before
   anything else, including the existing per-severity opt-in check.
   Mailing tamper-attribution detail — which can name a real person as
   the operator who edited or deleted a row — to an address nobody's
   confirmed belongs to the account holder is exactly the kind of
   consequential send verification exists to gate.

The gate for (1) and (2) lives in ONE place:
`permissions.REQUIRES_VERIFIED_EMAIL`, a frozenset of `Permission`
values checked inside `require_permission` itself, immediately after
the existing role-rank check and before returning. Every endpoint that
already calls `require_permission(user_id, org_id, permission)` for one
of those two permissions gets the gate automatically — `main.py`'s
`POST /orgs/{id}/invitations` and `POST /api-keys` handlers needed **zero
code changes** to pick this up, the same "one enforcement point, not one
per call site" reasoning `permissions.py`'s own module docstring already
states for the role-rank check ("Phase 2 had exactly one role check in
the whole codebase... copy-pasted as needed... stops being viable").

The role check runs first, deliberately: a caller who ALSO lacks the
required rank sees `INSUFFICIENT_ROLE`, not `EMAIL_NOT_VERIFIED` — the
more fundamental reason they can't do this, not a confusing second
reason for an action they couldn't have performed anyway even once
verified.

## Alternatives considered

- **Check `current_user.email_verified` inline at each affected
  endpoint.** Rejected for the same reason the original role-rank
  design was rejected in Phase 3 — see `permissions.py`'s own docstring.
  A new gated action added later, at a new call site, has to remember
  this rule exists; with it centralized in `require_permission`, a new
  `Permission` only needs adding to `REQUIRES_VERIFIED_EMAIL` if it
  should be gated, and every existing caller of `require_permission`
  already goes through the check unconditionally.
- **Block unverified accounts from writing anything at all** (e.g. also
  `RUN_CREATE`, `STEP_WRITE`). Rejected as broader than the actual risk:
  an unverified account creating its OWN runs/steps in ITS OWN project
  doesn't extend trust to anyone else the way inviting a member or
  minting an API key does. The three items above are specifically the
  ones where an unverified account's action reaches someone or
  something outside itself — a new invitee, a machine credential that
  outlives this session, or an email to a real person naming another
  real person.
- **Gate alert emails via a NotificationPreference-style opt-in the user
  sets themselves**, rather than an unconditional filter. Rejected — an
  unverified account choosing "yes, email me" doesn't establish the
  underlying fact (that the address is genuinely theirs) any more than
  not choosing it would; the existing per-severity preference checks
  already cover "did they ask for this," or torch this is a different,
  prior question ("can this address even be trusted with the content").

## Consequences

- A newly-signed-up owner (the common case, per
  `scripts/e2e_demo.py`'s Stage 1) cannot invite their first teammate or
  mint their first API key until they verify — a real, deliberate
  friction point, not an oversight. `docs/e2e-walkthrough.md`'s Stage 1
  documents the expected sequence (verify, THEN invite/mint).
  Interactively, this is one email click; for a fully automated
  signup-and-provision flow (no human to click a link), the caller needs
  a real path to redeem the verification token programmatically before
  proceeding to those two calls — the same constraint any email-
  verification design imposes.
- `permissions.REQUIRES_VERIFIED_EMAIL` is a frozenset checked by
  identity/membership (`in`), not a table lookup — adding a new gated
  `Permission` later is a one-line change with no migration.
