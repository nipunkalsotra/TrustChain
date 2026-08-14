# 0001 — Transactional outbox for anchoring

**Status:** Accepted

## Context

Every agent step needs to eventually be anchored on-chain, but writing
to Postgres and writing to a blockchain can't happen atomically — no
distributed transaction spans the two. If the API process wrote the
`steps` row and then, separately, tried to anchor it (directly, inline,
in the request path), a crash between those two actions is
unobservable from outside: the step exists in the database with no
record that anchoring was ever attempted, and nothing would ever retry
it. A step could silently never get anchored, with no error surfaced
anywhere.

Anchoring inline in the request path also couples the API's response
latency to on-chain confirmation time (seconds, not milliseconds), and
couples one tenant's request to whatever the chain happens to be doing
at that moment (congestion, gas spikes).

## Decision

Every `steps` INSERT happens in the **same Postgres transaction** as an
`anchor_outbox` row recording durable intent to anchor it
(`agents/base.py::log_step`). A separate process (the anchor worker)
polls `anchor_outbox` for `pending` rows, claims them, and anchors them
asynchronously. Either both rows commit or neither does — there is no
state where a step exists without a corresponding intent-to-anchor
record.

## Alternatives considered

- **Anchor inline in the request path.** Rejected: couples API latency
  to chain confirmation time, and a crash mid-anchor loses the step
  with no recovery signal.
- **Fire-and-forget background task, no outbox table.** Rejected: a
  crashed process loses any in-memory queue of pending work with no
  record it ever existed. This is exactly the "unobservable failure"
  problem the outbox exists to close.
- **Change Data Capture (CDC) off the `steps` table** (e.g. Debezium
  reading Postgres's WAL) instead of an explicit outbox table. Rejected
  as unnecessary operational complexity for this scale — a polled
  outbox table with `SELECT ... FOR UPDATE SKIP LOCKED` claiming gives
  the same at-least-once guarantee with no extra infrastructure.

## Consequences

- Anchoring is **at-least-once**, not exactly-once — a worker crash
  between claiming a row and confirming its anchor requires the reaper
  (`anchor_worker/reaper.py`) to detect the orphaned claim (timeout-based)
  and requeue it. A requeued step gets anchored again if the original
  attempt actually succeeded on-chain but the worker crashed before
  recording that — harmless (the contract's `usedRoots` check prevents
  double-counting the same root), just occasionally redundant.
- Anchoring latency is now "eventually, within one poll interval" rather
  than "before the API responds" — the API's `POST /steps`/pipeline
  responses don't block on chain confirmation, at the cost of a caller
  needing to poll `GET /steps/{id}/proof` (404 until anchored) rather
  than getting a proof back immediately.
- The reconciliation half of this pattern lives in the indexer
  (`indexer/reconcile.py`) — a batch whose local row is stuck at
  `submitted` (worker crashed after sending the transaction but before
  recording its own confirmation) gets its status corrected by the
  indexer observing the `BatchAnchored` event independently, closing
  the other half of the crash window this design creates.
