# 0003 — V1→V2 contract migration: read-only V1, fresh V2, no in-place upgrade

**Status:** Accepted

## Context

V1's contracts (`AgentAuditLog`, `AgentIdentityRegistry`,
`TrustScoreRegistry`) are single-tenant, anchor per-step (see ADR-0002),
and were already deployed to real Monad testnet with real transaction
history before multi-tenancy, Merkle batching, and the outbox pattern
were designed. None of that can be retrofitted onto already-deployed,
immutable contract bytecode — Solidity contracts aren't upgradable
unless they were built with a proxy pattern from day one, and V1
wasn't.

## Decision

V2 is a **fresh set of contracts**, deployed independently, with no
shared storage or migration path from V1. V1 stays deployed and
untouched, demoted to **read-only** — it still serves `/verify` and
`/verify/tamper-demo` (proving on-chain immutability by re-reading the
chain directly is the whole point of those two endpoints; routing them
through a cache/V2 would defeat that), but nothing writes to it anymore.
All new activity (multi-tenant runs, Merkle-batched anchoring, agent
identity, trust scores) goes through V2 exclusively.

## Alternatives considered

- **Deploy a proxy in front of V1 and "upgrade" it.** Not possible after
  the fact — V1's storage layout and lack of a delegatecall proxy mean
  there's no live contract to upgrade; this would require redeploying
  anyway, at which point it's not actually an upgrade, just a rename of
  the "fresh deploy" option below it.
- **Migrate V1's historical data into V2 at deploy time.** Rejected:
  V1's per-step, single-tenant records don't map cleanly onto V2's
  batched, multi-tenant model (there's no "project" for pre-multi-tenancy
  V1 records to belong to), and V1's own audit trail is more valuable
  left exactly as it was originally anchored than reinterpreted into a
  different schema after the fact.
- **Retire V1 entirely once V2 ships.** Rejected for now: `/verify` and
  the tamper demo are a real, working, on-chain-verified feature —
  turning them off loses working functionality for no benefit, and V1
  costs nothing to leave running (no writes, no maintenance).

## Consequences

- Two chain connections exist side by side in the API process
  (`blockchain/client.py`'s `BlockchainBridge` for V1, `anchor_worker/chain.py`
  for V2) — `config.py`'s `v2_rpc_url`/`v2_private_key` fields exist
  specifically because V1 and V2 can legitimately point at different
  chains during this transition (V1 stays on real Monad testnet where it
  was originally deployed; local dev deploys V2 fresh to Anvil each
  time).
- `TrustChainRegistry` (added later, see `docs/architecture.md`) exists
  specifically to make a THIRD contract generation, if one is ever
  needed, resolvable by version number rather than requiring another
  ad hoc "V3" naming convention hardcoded into every consumer.
- Anyone auditing a pre-V2 run needs to know which generation's contract
  their proof was anchored against — the two are not interchangeable,
  and nothing currently surfaces "V1 vs V2" as an explicit field on a
  run (it's implicit in when the run happened). A future cross-generation
  query surface would need to make this explicit.
