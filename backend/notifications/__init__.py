"""
notifications/ — email delivery for TrustChain alerts and invitations
(Phase 3 §7.4).

- backends/  — pluggable send-one-email implementations (console/smtp/
  ses/memory), selected by config.Settings.email_backend. Mirrors
  blockchain/signer.py's pluggable signing backends (ADR-0008).
- templates.py — plain-text/HTML content rendering for each email kind.
- sender.py  — the outbox-claiming loop that drains alert_deliveries
  (runs inside integrity_watchdog's process, see that module's
  __main__). Retries with exponential backoff, dead-letters after
  config.alert_delivery_max_attempts.
- invite.py  — the one email kind sent inline rather than through the
  outbox (see its own module docstring for why).
"""
