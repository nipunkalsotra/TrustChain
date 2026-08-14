# 0002 — Merkle batching, not per-step anchoring

**Status:** Accepted

## Context

V1 anchored every single agent action with its own transaction
(`AgentAuditLog.logAction`). That's simple and gives an immediate,
one-step-one-tx audit trail, but it means gas cost scales linearly with
step count and every step pays full transaction overhead individually —
untenable once a platform is anchoring steps from many tenants' pipeline
runs concurrently, and directly punishes exactly the workload TrustChain
is meant to support (agents that take many small steps).

## Decision

V2's anchor worker groups pending outbox rows into batches, builds a
Merkle tree over each batch's step leaf hashes, and submits only the
**root** on-chain via `AgentAuditLogV2.anchorBatch(runIdHash, root,
stepCount, metaURI)` — one transaction anchors an entire batch,
regardless of size. A step's individual inclusion proof (the sibling
hashes needed to recompute the root from its leaf) is reconstructed
off-chain, on demand, from the batch's persisted `leaf_order`
(`db/read_model.py::get_step_proof`), and can be verified either purely
locally (recompute the root, compare — trusts the API's stated root) or
against the real deployed contract's `verifyProof()` (trusts nothing but
the chain).

## Alternatives considered

- **Per-step anchoring (V1's model), kept for V2 too.** Rejected: gas
  cost scales with step count, directly punishing high-step-count
  agent runs — the opposite of what a platform anchoring many tenants'
  steps needs.
- **Batch anchoring with the full leaf set stored on-chain** (not just
  the root). Rejected: on-chain storage is roughly 10x the cost of
  event log data for the same bytes: the batch's `metaURI` field exists
  specifically so a caller CAN publish the full leaf set to
  IPFS/Arweave for independent public verifiability without paying
  storage-tier gas costs for it (see `anchor_worker/submit.py`'s
  `meta_uri` docstring) — not committing the full set on-chain by
  default, while leaving the door open to.
- **A Merkle Mountain Range or other append-only accumulator** instead
  of rebuilding a tree per batch. Rejected as unneeded complexity: batch
  boundaries are already natural (whatever's pending when the worker
  polls), and rebuilding a fresh tree per batch is simple to reason
  about and to test byte-for-byte against a pure-Python/TypeScript
  reference implementation (see `blockchain/merkle.py` and the SDKs'
  `merkle.py`/`merkle.ts`).

## Consequences

- A step's proof is only available once its *batch* confirms, not
  immediately when the step is logged — `GET /steps/{id}/proof` 404s
  until then (see ADR-0001's latency tradeoff).
- `leaf_order` must be persisted, not re-derived by re-querying
  `steps` later — a "re-sort by created_at" approach would silently
  break if the query or table shape ever changed, producing a
  different tree than the one that was actually anchored. Persisting
  the exact ordered leaf list makes the proof reconstruction provably
  correct regardless of later schema changes.
- Batch failure (revert, confirmation timeout) detaches the whole
  batch's steps and requeues them for rebatching (or dead-letters them
  past `ANCHOR_MAX_ATTEMPTS`) — one bad batch doesn't lose any step, but
  does mean a single problematic step (e.g. one that somehow causes a
  revert) can repeatedly disrupt whichever batch it lands in until it's
  isolated. Not yet hit in practice; `anchor_worker/main.py::handle_submit_failure`
  is the mechanism that would need extending if it were (e.g.
  quarantining a specific step after N batch failures involving it).
