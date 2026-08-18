# 0015 — Tiered continuous verification

**Status:** Accepted

## Context

Every existing verification path in TrustChain (`GET /agents/{id}/verify`,
`GET /steps/{id}/proof`) is pull-based — something external has to call
it. Nothing re-checks an anchored step or a registered agent's identity
on its own. That's fine for "prove this specific thing on demand" but
does not deliver the product's actual Phase 3 promise: "tell owners and
admins when tampering happens," which requires *something* to be
looking, continuously, without being asked.

The naive version of "continuously" — re-verify all of history, every
cycle — costs one RPC call per agent and a full Merkle rebuild per batch,
**every single cycle, forever**. That is precisely the O(N)-over-all-
history mistake Phase 2's entire read-path effort (event-sourced
indexer, `rm_scores`) existed to eliminate for reads; repeating it for
verification would make detection cost grow without bound as the
product succeeds and accumulates history.

## Decision

Three tiers, matched to what each is actually for:

- **Hot** — every cycle (default 60s), everything created/confirmed in
  the last `watchdog_hot_window_seconds` (default 2h). Small and
  bounded by construction; this is what makes detection latency for
  *fresh* tampering roughly one poll interval, not "eventually, when the
  rolling sweep gets there."
- **Rolling** — a persistent cursor (`watchdog_cursor`, one row per
  detector) walks *all* of history at a **fixed row budget per cycle**
  (`watchdog_rolling_steps_per_cycle`/`..._batches_per_cycle`), wrapping
  to the start on completion. A full pass takes longer as history grows;
  the cost of any *one* cycle does not — the system slows its coverage
  cadence gracefully under growth instead of falling over.
- **On-demand** — `POST /integrity/verify-run/{run_id}` runs every
  detector against one run synchronously, for the "is this specific run
  still intact right now" question a person is actively asking, outside
  the sweep loop entirely.

Detector 4(c) — comparing a batch's stored root against the real
on-chain root — is the only check here that costs an RPC call. Once
verified, the result is cached in `batch_verifications` and never
re-checked: an anchored batch's on-chain root is immutable, so there is
nothing to gain from asking twice, only RPC cost. Steady-state RPC load
is therefore proportional to *new* confirmed batches per cycle, not to
total history.

A single Postgres advisory lock (`integrity_watchdog/lock.py`,
`config.watchdog_advisory_lock_key` — a distinct key from
`anchor_worker/nonce_lock.py`'s) ensures only one watchdog instance
actively sweeps; an accidental second instance idles rather than
duplicating alerts.

## Alternatives considered

- **Full sweep every cycle.** Rejected — see Context; violates the
  Phase 3 plan's own stated goal (§3.1) "anchoring cost and latency
  decouple from step count," applied to verification instead of
  anchoring.
- **Event-driven only (detectors 1/2), no periodic sweep at all.**
  Catches silent model swaps (write-path) and on-chain identity events
  (indexer-driven) with zero periodic cost — but has no answer for T3/T4
  (a step or batch edited/deleted directly in Postgres, with no
  triggering "event" to hook at all). The rolling tier exists
  specifically because those attacks have no event to be driven by.
- **A separate watchdog process per detector.** More moving parts to
  operate for no isolation benefit the shared advisory lock and shared
  Postgres connection don't already provide; one process, one loop, one
  failure mode to reason about.

## Consequences

- `GET /integrity/status`'s `coveragePercent` and
  `full_sweep_age_seconds` are honest signals of degraded coverage under
  load, not just liveness — `docker/prometheus/alerts.yml`'s
  `TrustChainFullSweepStale` rule watches the latter directly.
- The rollout plan (Phase 3 plan §16) explicitly warns that the FIRST
  full rolling pass over pre-Phase-3 history will surface legitimate
  historical noise (Anvil resets, dev-only rows) before it's ever safe
  to enable email — a direct consequence of "the rolling tier eventually
  reaches everything," including data nobody meant to be verified
  against a chain that may no longer exist.
- Pre-anchor tampering (a step edited in the seconds between being
  written and being included in a batch) is invisible to every detector
  here by construction — it becomes part of the committed root before
  any check runs. This is the same cost/latency tradeoff Merkle batching
  itself makes (ADR-0002), inherited rather than reintroduced.
