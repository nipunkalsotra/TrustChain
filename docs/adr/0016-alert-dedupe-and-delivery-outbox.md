# 0016 — Alert dedupe and delivery outbox

**Status:** Accepted

## Context

A watchdog that re-checks the same historical steps/batches every
rolling-tier pass (ADR-0015) will, if nothing else changes, re-discover
the *same* unresolved problem on every pass. Naively calling "raise an
alert" on every discovery would re-notify on every cycle — the Phase 3
plan (§7.2/§16) is explicit that this alone would make TrustChain email
unusable within a day, training every recipient to ignore it before it
ever catches something real. Separately, raising an alert and actually
telling a human about it are two different failure domains — the
process that *finds* a problem is not necessarily still running (or
network-connected to an SMTP/SES endpoint) at the moment it needs to
*send* mail about it.

## Decision

**Dedupe:** `alerts.dedupe_key = sha256(alert_type : scope : subject)`
(`db/alerts.py::compute_dedupe_key`), with a **partial unique index**
(`uq_alerts_open_dedupe`, `WHERE status = 'open'`) enforcing at most one
*open* alert per key. `raise_alert()` is UPSERT-shaped: an existing open
alert with the same key gets `occurrence_count += 1` and
`last_seen_at` bumped; only a genuinely new key creates a new row (and
only a new row enqueues fresh `alert_deliveries`). Resolving an alert
frees its key, so a real recurrence after a real fix raises a fresh one.

**Delivery:** `alert_deliveries` is a transactional outbox, written in
the **same transaction** as the `alerts` row it belongs to — deliberately
reusing the exact pattern `agents/base.py::log_step` already applies to
`steps`+`anchor_outbox` (ADR-0001): a crash between "alert recorded" and
"delivery queued" must be impossible to observe from outside.
`notifications/sender.py` claims rows with `FOR UPDATE SKIP LOCKED`
(the same primitive `anchor_worker/claim.py` uses for anchor_outbox),
retries with exponential backoff, and dead-letters after
`alert_delivery_max_attempts` — a dead-lettered delivery itself becomes
a log-visible event, since "we failed to tell you something important"
must not fail silently.

## Alternatives considered

- **Send email synchronously from `raise_alert()` itself.** Couples
  every alert-raising call site (the SDK's write path, the indexer, the
  watchdog) to SMTP/SES latency and failure modes — a slow or down mail
  provider would then also slow down or break step logging and chain
  indexing. The outbox exists specifically to decouple "did we durably
  record that something needs to happen" from "did the thing that makes
  it happen work this time."
- **Dedupe by alert `id` reuse (update the same row's fields in place
  on recheck) instead of a dedupe key.** The dedupe key approach was
  chosen so a NEW, semantically-different problem with the same subject
  (e.g. a step that was tampered with, fixed, then genuinely
  re-tampered) is distinguishable from a continuously-recurring one —
  resolving frees the key, closing that gap.
- **Time-window dedupe (suppress repeats within N minutes) instead of
  open-alert dedupe.** Rejected — a time window either fires again while
  the problem is still unresolved (defeating the purpose) or suppresses
  a genuine NEW occurrence that happens to land inside the window after
  the old one was resolved. Keying off `status='open'` ties suppression
  to the actual state of the problem, not to a clock.

## Consequences

- Email for a *recurring* open alert is still throttled separately
  (`alert_email_throttle_seconds`, checked against `last_emailed_at` by
  the sender) — dedupe stops duplicate ROWS; throttling stops duplicate
  SENDS for a legitimately still-open, still-recurring one.
- `alert_deliveries` has no `org_id`/`project_id` of its own — it's
  RLS-scoped via a join to `alerts` (migration `d7e8f9a0b1c2`), the same
  shape `9f3a1c7d5e2b` already established for `steps` → `runs`.
- The watchdog process runs `notifications/sender.py`'s drain loop
  in-process (`integrity_watchdog/main.py`) rather than as a fourth
  container — one more always-on background task inside an
  already-always-on process, not a new deployment unit, consistent with
  the single-EC2-instance deploy target this phase was scoped against.
