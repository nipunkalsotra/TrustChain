# 0012 — Default deployment posture for multisig admin

**Status:** Accepted

## Context

Plan §11.2's own Phase 1 → Phase 2 diagram is explicit about the target
end state: `DEFAULT_ADMIN_ROLE` on all three V2 contracts held by a
2-of-3 Gnosis Safe, cold and offline, never a single EOA — "today a
single externally-owned account owns all three contracts and holds the
hot signing key. Anyone obtaining that key can write false 'immutable'
records — which invalidates the product's central claim."

Before this task, `DeployV2.s.sol` deployed all four V2 contracts
(`AgentAuditLogV2`, `TrustScoreRegistryV2`, `AgentIdentityRegistryV2`,
`TrustChainRegistry`) with `DEFAULT_ADMIN_ROLE` held by the deployer EOA,
full stop. Moving to a Safe was a *documented but entirely separate,
manual* follow-up (`TransferAdminToMultisig.s.sol`, see
`docs/multisig-admin-handoff.md`) that a human has to remember to run.
That's a real gap between the default and the plan's own stated target:
nothing stops a real deployment from running `DeployV2.s.sol`, going
live, and simply never running the second script — the single-EOA
window the plan calls out as the most important security change in
Phase 2 stays open indefinitely by default, not for the few minutes a
conscious handoff would take.

## Decision

`DeployV2.s.sol` gained an optional `SAFE_ADDRESS` env var:

- **Set:** the deployer still briefly holds `DEFAULT_ADMIN_ROLE` on all
  four contracts (unavoidable — granting `ANCHOR_ROLE`/`REGISTRAR_ROLE`
  to the relayer requires being `DEFAULT_ADMIN_ROLE`'s holder, and these
  contracts' constructors take one `admin` address, not a separate
  relayer param), but only for the remaining duration of that one script
  execution. Immediately after granting the relayer its roles, the
  script grants `DEFAULT_ADMIN_ROLE` to the Safe, verifies the grant
  landed on-chain, then renounces the deployer's own
  `DEFAULT_ADMIN_ROLE` on all four contracts — the exact grant-verify-
  renounce sequence `TransferAdminToMultisig.s.sol` already used, now
  folded into the SAME atomic script run instead of a second, separate
  one a human has to remember to invoke. Verified for real against a
  local Anvil chain: `cast call ... hasRole(DEFAULT_ADMIN_ROLE, safe)`
  returns `true`, the same call for the deployer returns `false`, and
  `hasRole(ANCHOR_ROLE, relayer)` still returns `true` — the relayer's
  operational role is unaffected by who holds admin.
- **Unset:** falls back to the original deployer-EOA-admin behavior,
  permanently, with a loud `console.log()` warning explaining the
  tradeoff and pointing at `TransferAdminToMultisig.s.sol` as the manual
  follow-up. This stays the default specifically because local dev
  against a fresh Anvil chain has no persistent Safe to point at — Anvil
  resets to genesis on nearly every `docker compose up --build` in this
  repo's own dev workflow (see this repo's Docker-Anvil-reset notes
  elsewhere), so a Safe deployed for one Anvil session wouldn't even
  exist for the next. Requiring `SAFE_ADDRESS` unconditionally would
  break the zero-config local workflow every other doc in this repo
  (README, `docs/multisig-admin-handoff.md`, this project's own
  `DeployV2.s.sol` docstring) already assumes.

## Alternatives considered

- **Constructor takes the Safe directly, deployer never holds admin at
  all.** Tried first; reverted immediately with a real
  `AccessControlUnauthorizedAccount` error the moment the script tried
  to `grantRole(ANCHOR_ROLE, relayer)` — found via `forge test`, not by
  inspection. `DEFAULT_ADMIN_ROLE`'s holder is the only address that can
  grant any other role by default in OpenZeppelin `AccessControl`, so a
  deployer that's never admin can't grant the relayer anything either.
  Fixing this properly would mean changing all three contracts'
  constructors to accept a relayer address too — a bigger, more
  invasive change to already-deployed contracts' constructor ABI than
  this task's actual gap warranted, for a security benefit (shrinking an
  already-sub-one-block window to zero) that's marginal next to the real
  gap being closed (an unbounded, easy-to-forget window shrinking to a
  few seconds).
- **Require `SAFE_ADDRESS` unconditionally, no EOA-admin fallback at
  all.** Rejected — breaks local dev against Anvil, which has no
  standing Safe infrastructure and resets on nearly every rebuild in
  this repo's own workflow. A hard requirement here would make the
  single most common path through this script (local development)
  impossible without deploying a throwaway Safe every session, for a
  security property (protecting funds/data with real value behind it)
  that local Anvil deployments don't have to protect.
- **Leave `DeployV2.s.sol` as-is and only strengthen documentation/
  process** (e.g. a checklist item, a deploy pipeline gate requiring a
  human to confirm the handoff ran). Considered, and it's a reasonable
  complementary control, but it doesn't change the DEFAULT — a
  deployment that skips the checklist or the gate is still left with a
  single EOA holding total authority, silently, exactly the failure mode
  this decision exists to close. A safer default beats a
  documented-but-optional follow-up.

## Consequences

- Any real deployment (testnet with real value, mainnet-equivalent
  chain) can now reach the plan's Phase 2 target state
  (`DEFAULT_ADMIN_ROLE` on a Safe) in ONE script invocation with a
  pre-existing `SAFE_ADDRESS`, instead of two invocations with a manual
  verification step in between that's easy to skip or delay.
- The deployer key still needs enough native token to pay gas for the
  handoff transactions (4 contracts × grant + renounce = 8 extra
  transactions) in the same run — a marginal cost increase over the
  admin-stays-EOA path, not a new operational burden (the deployer
  already needed gas for the 4 deployment + role-grant transactions).
- `docs/multisig-admin-handoff.md` and `TransferAdminToMultisig.s.sol`
  remain necessary and correct for the one case this decision doesn't
  cover: a contract set already deployed with `SAFE_ADDRESS` unset (this
  repo's own local-dev default, and any pre-existing real deployment
  from before this decision) that needs to move to a Safe *after the
  fact*. This decision changes what NEW deployments default to; it does
  not retroactively fix already-deployed contracts, which still need the
  separate handoff script.
- The local-dev default (`SAFE_ADDRESS` unset, admin = deployer EOA) is
  unchanged and remains, by design, not how this should be deployed
  anywhere with real value behind it — this decision makes the SAFER
  path a single flag away, it doesn't make the local-dev path safer
  (nor should it: local Anvil deployments have nothing real to protect).
