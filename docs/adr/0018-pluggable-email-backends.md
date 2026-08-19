# 0018 — Pluggable email backends

**Status:** Accepted

## Context

Alert email needs to work in three very different situations that
should all use the same code path: a fresh local checkout with zero
credentials configured (must not fail, must not silently try to send
real mail), an early-integration deployment testing against a personal
Gmail account, and the real production target — a single EC2 instance
where SMTP credentials sitting in an env file are worse than
unnecessary (an IAM instance role can authorize AWS SES directly,
no long-lived secret on the box at all).

## Decision

`notifications/backends/` mirrors `blockchain/signer.py`'s pluggable
local/KMS/Vault signing backends (ADR-0008) exactly: one narrow
`EmailBackend` Protocol (`send(to, subject, text_body, html_body) ->
SendResult`), one factory keyed off `config.email_backend`
(`get_backend()`), so swapping `console -> smtp -> ses` is a config
change, not a code change.

- **`console`** (default) — renders to the structured logger, sends
  nothing. A fresh checkout needs no email credentials to run at all.
- **`smtp`** — stdlib `smtplib` only (no `aiosmtplib` dependency); the
  actual send runs via `asyncio.to_thread` since `smtplib` is
  synchronous. Works with a Gmail app password for early testing.
- **`ses`** — the production choice for the EC2 target. `boto3`'s
  `sesv2` client, also run via `asyncio.to_thread`. No SMTP credentials
  need to live on the instance — an attached IAM role is sufficient.
- **`memory`** — test-only, appends sent messages to a module-level
  list for assertions.

## Alternatives considered

- **A single hardcoded SMTP integration.** Would force a real SMTP
  server (or credentials for one) to exist just to run the app locally
  or in CI, and would mean migrating to SES later is a code change
  instead of a config change — exactly the coupling ADR-0008 already
  rejected for the signing key.
- **A third-party transactional-email SaaS SDK (e.g. SendGrid,
  Postmark) as the only backend.** Adds an external account dependency
  this repo cannot provision for the user, and doesn't fit the
  EC2-instance-role deployment story as cleanly as SES does for a stack
  already targeting AWS.
- **`aiosmtplib` instead of stdlib `smtplib` + `asyncio.to_thread`.**
  Would avoid the thread-offload, but adds a runtime dependency for a
  backend that's explicitly a stepping-stone to `ses` in production, not
  the long-term answer — not worth it for what's meant to be a
  temporary/early-testing path.

## Consequences

- `docker-compose.yml`'s `integrity-watchdog` service (which runs
  `notifications/sender.py`'s drain loop) sets `EMAIL_BACKEND=console`
  explicitly, even though that's already the default — so nothing in
  the local dev stack ever silently sends real email regardless of what
  else changes in `.env`.
- Going to production requires REAL prerequisites this code cannot
  satisfy on its own: a verified SES sending domain, SPF/DKIM/DMARC DNS
  records, and exiting the SES sandbox (which otherwise only permits
  sending to addresses that are themselves verified). Documented as a
  rollout step (Phase 3 plan §16 step 6), not assumed done — a
  deployment that flips `email_backend=ses` before completing these will
  see delivery fail, not silently succeed.
- A new backend (a fourth provider, say) needs to implement one
  `Protocol` method and register in `get_backend()` — no other file in
  `notifications/` needs to know it exists.
