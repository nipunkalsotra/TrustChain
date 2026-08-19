# 0022 — Email verification and password reset tokens

**Status:** Accepted

## Context

Phase 4 closed two real gaps flagged as blockers (G1/G2): there was no
way to confirm a signup's email address belonged to the person
registering it, and no recovery path for a forgotten password. Both need
a bearer credential minted server-side, delivered by email, and redeemed
exactly once — the same shape ADR-0014 already solved for invitations.

## Decision

`email_verification_tokens` and `password_reset_tokens`
(`db/email_verification.py`/`db/password_reset.py`) follow ADR-0014's
invitation-token discipline exactly, not a new pattern: only
`sha256(token)` is stored, the raw token exists exactly once (in the
email body), single-use is enforced by a conditional
`UPDATE ... WHERE used_at IS NULL` (not a read-then-write, so two
concurrent redemption attempts can't both succeed), and both tables are
user_id-scoped with no RLS policy — a user's identity/credential state
isn't tenant data, the same reasoning `refresh_tokens` and `users`
themselves already establish.

Two deliberate departures from the invitation-token shape, both because
the token's purpose differs:

- **Two separate tables, not one generic "account token" table.**
  ADR-0014's invitations, these two, and refresh tokens are each their
  own table rather than one polymorphic token table with a `kind`
  column. A generic table would need every consumer to filter by kind
  correctly forever, exactly the kind of "one shared thing four
  different call sites all depend on getting right" this codebase
  otherwise deliberately avoids (see e.g. `errors.py`'s per-call-site
  `ErrorCode`s instead of one generic error string). Two small,
  single-purpose tables cost nothing extra and can't be misused.
- **Different TTLs, chosen for what each token actually authorizes**
  (`config.email_verification_ttl_seconds`, default 24h;
  `config.password_reset_ttl_seconds`, default 1h — both shorter than
  an invitation's 7 days). A reset token is a live account-takeover
  credential if intercepted; a verification token only proves inbox
  control. Both are meaningfully more sensitive than "join an org I
  already control access to," which is what makes an invitation safe to
  leave valid for a week.

**Signing up via a valid invitation sets `email_verified=true`
immediately** (`db.create_user`), skipping a redundant verification
email entirely — redeeming a real, single-use, emailed invitation link
is itself proof of inbox control, at least as strong as the standalone
verify-email click (arguably stronger: it's also tied to a specific
admin's decision to send it to exactly that address). Found necessary by
a real end-to-end run (`scripts/e2e_demo.py`'s Stage 3): without this,
an invited admin was immediately blocked by
`permissions.REQUIRES_VERIFIED_EMAIL` (see ADR-0023) from acting on the
very permissions their invitation had just granted.

**`POST /auth/forgot-password` never reveals whether the account
exists** — returns the identical `{"ok": true}` regardless
(`db.password_reset.create_reset_token_if_exists` is the *only* place
"does this email exist" is ever branched on; the HTTP handler never
sees that branch, so a future edit to it can't reintroduce an
enumeration oracle by checking existence itself first). A successful
reset revokes every existing refresh-token family for that user
(`refresh.revoke_all_for_user`) — a session opened before the password
changed must not silently keep working after.

## Alternatives considered

- **One shared "account action token" table with a `kind` enum.**
  Rejected — see "two separate tables" above.
- **Verifying email at signup time by requiring an OTP before the
  account is even created**, rather than creating an unverified account
  and gating specific actions on it. Rejected: it would make signup a
  two-round-trip flow for every caller, including SDK/CLI-driven
  automation that has no interactive step to wait on an OTP in, for a
  security property (confirming inbox control) that a post-signup gate
  achieves just as well without forcing that shape on every caller.
- **Reusing the invitation token's TTL (7 days) for these two.**
  Rejected — see "different TTLs" above; the risk profile genuinely
  differs.

## Consequences

- A user who never verifies stays a real, usable account (can log in,
  read) forever — there's no expiry sweep that locks out a stale
  unverified signup. `db.email_verification.cleanup_expired` exists
  (mirroring `db/idempotency.py`'s 24h cleanup job shape) but isn't
  wired into a scheduler by this change; adding that is additive, not a
  redesign, the same way the idempotency-key cleanup job's own scheduler
  wiring can be added independently of the table it cleans.
- See ADR-0023 for what an unverified account is actually blocked from
  doing, and why the check lives in `permissions.py` rather than at each
  affected endpoint.
