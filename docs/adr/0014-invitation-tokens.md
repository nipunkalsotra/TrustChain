# 0014 — Invitation tokens

**Status:** Accepted

## Context

Before Phase 3, `Membership` rows were only ever created by
`db.tenancy.provision_personal_org` at signup — there was no way for a
second person to join an existing org at all. Adding one means minting
a bearer credential (a link an admin sends to someone's email) that
grants real membership authority the moment it's redeemed — the same
category of risk as an API key or a refresh token, not a cosmetic
feature.

## Decision

`invitations` follows the exact discipline `ApiKey.key_hash` /
`RefreshToken.token_hash` already established: only `sha256(token)` is
stored (`db/invitations.py::_hash_token`), the raw token exists exactly
once, in the email body, and is never logged or returned by any other
endpoint. Single-use is enforced by a **conditional `UPDATE ... WHERE
accepted_at IS NULL`** (`mark_accepted`/`accept_for_existing_user`), not
a read-then-write — two concurrent accept attempts both read the same
"not yet accepted" state, but only one `UPDATE` can match the `WHERE`
clause; the loser's `rowcount` is 0 and is treated as an already-used
token, never a silent double-accept. Tokens expire
(`invitation_ttl_seconds`, default 7 days) and are revocable by an
admin before use.

`role` can never be `"owner"` — enforced by a `CHECK` constraint
(`ck_invitations_role`) restricting it to `admin|member|viewer`.
Ownership is **transferred**, not granted from nothing
(`POST /orgs/{id}/transfer-ownership`, `db/orgs.py::transfer_ownership`,
atomic promote-target/demote-actor in one transaction) — allowing an
invitation to mint a second owner would make "how many owners does this
org have" a function of who happened to accept what, exactly the
ambiguity last-owner protection (`CANNOT_REMOVE_LAST_OWNER`) needs to
reason about cleanly.

A signup through a valid `invite_token` (`POST /auth/signup`) routes
through `db.tenancy.join_org_via_invitation` instead of
`provision_personal_org` — the new user joins the inviter's org and
does **not** also get an empty personal org of their own.

The public preview (`GET /invitations/{token}`) is deliberately
unauthenticated (the recipient has no account yet) and returns one
identical 404 for an invalid, expired, revoked, *or already-accepted*
token — a scanner probing token guesses learns nothing from which of
those four is actually true.

## Alternatives considered

- **Store the raw token.** Rejected for the same reason `ApiKey`/
  `RefreshToken` don't — a leaked database dump would hand out live
  invitation links.
- **Optimistic locking (read `accepted_at`, check in Python, then
  write) instead of the conditional `UPDATE`.** Loses the single-use
  guarantee under real concurrency — a classic TOCTOU race between two
  simultaneous `POST /invitations/{token}/accept` calls.
- **Let an invitation grant `owner`.** Rejected — see Decision.
- **Distinguish "invalid" from "expired" from "already used" in the
  preview response**, for a better error message. Rejected — the
  enumeration-safety property (Phase 3 plan §5.1) is worth more than a
  marginally clearer message; the actual acceptance flow's real errors
  (`INVITATION_EXPIRED`, `INVITATION_ALREADY_ACCEPTED`, etc.) still
  exist for the authenticated accept path, where the caller already
  proved they hold the (valid, at some point) token.

## Consequences

- A partial unique index (`uq_invitations_pending`, `WHERE accepted_at
  IS NULL AND revoked_at IS NULL`), not a plain `UniqueConstraint`, is
  what enforces "at most one pending invitation per (org, email)" — a
  plain constraint can't express "unique only when these columns are
  both NULL". `POST /orgs/{id}/invitations/{id}/resend` is implemented
  as revoke-then-recreate specifically so it reuses this same
  constraint and rate limiting rather than needing its own path.
- `invitations` is RLS-scoped by `org_id` like every other new
  org-level table (migration `d7e8f9a0b1c2`) — but the public preview
  and the invite-token signup validation both need an explicit
  `rls_bypass()` (`db/engine.py`) since neither has an
  `app.current_org_id` GUC to key a policy on; this is the same
  chicken-and-egg migration `9f3a1c7d5e2b` documents for `api_keys`,
  resolved the same way, in one audited place
  (`db/invitations.py::get_invitation_preview`/`validate_for_signup`).
