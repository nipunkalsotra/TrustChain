# Architecture Decision Records

Each ADR captures a decision that shaped TrustChain's architecture: the
context that forced a choice, what was decided, what else was
considered and why it lost, and the consequences (including the ones
that are honest tradeoffs, not just wins). See
[`docs/architecture.md`](../architecture.md) for how these decisions fit
together into the system as it actually exists today.

| # | Decision |
|---|---|
| [0001](0001-transactional-outbox-for-anchoring.md) | Transactional outbox for anchoring |
| [0002](0002-merkle-batching-not-per-step-anchoring.md) | Merkle batching, not per-step anchoring |
| [0003](0003-v1-to-v2-contract-migration-strategy.md) | V1→V2 contract migration: read-only V1, fresh V2, no in-place upgrade |
| [0004](0004-multi-tenancy-data-model.md) | Multi-tenancy data model (Organization/Project/Membership) |
| [0005](0005-api-versioning-via-dual-mounted-router.md) | API versioning via a dual-mounted router |
| [0006](0006-row-level-security-as-defense-in-depth.md) | Row-Level Security as defense-in-depth under a separate DB role |
| [0007](0007-sdk-instrumentation-library-not-just-a-wrapper.md) | SDKs are an instrumentation library, not just a REST wrapper |
| [0008](0008-pluggable-signer-backends.md) | Pluggable signer backends (local / KMS / Vault) |
| [0009](0009-rpc-fallback-and-circuit-breaker.md) | RPC fallback + circuit breaker |
| [0010](0010-jwt-issuer-and-audience-claims.md) | JWT issuer/audience claims |
| [0011](0011-canary-rollout-for-a-single-host-deploy.md) | Canary rollout for a single-host deploy |
| [0012](0012-multisig-default-deployment-posture.md) | Default deployment posture for multisig admin |

## Format

Each ADR follows: **Status** (Accepted — none have been superseded yet),
**Context** (the forcing constraint), **Decision**, **Alternatives
considered**, **Consequences** (including real tradeoffs, not just
benefits). New ADRs get the next sequential number and a PR that touches
the decision they describe should update the ADR's Consequences section
rather than silently drifting from it.
