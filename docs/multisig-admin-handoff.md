# Handing V2 contract admin to a Gnosis Safe

`DeployV2.s.sol` deploys `AgentAuditLogV2`, `TrustScoreRegistryV2`, and
`AgentIdentityRegistryV2` with `DEFAULT_ADMIN_ROLE` held by a single EOA
(the deployer). That's fine for local dev against Anvil; it is **not**
how any deployment with real value behind it should stay configured —
one private key being able to pause every contract, register/revoke
agents, and reset runs is exactly the single point of failure a
production security posture avoids.

`script/TransferAdminToMultisig.s.sol` moves that admin role to a Gnosis
Safe. This doc is the runbook for doing that safely.

## What this does and does not cover

**Does not cover:** deploying the Safe itself. Standing up a full Safe
(singleton + proxy factory + choosing owners/threshold) is its own,
separate, higher-stakes decision — go to
[app.safe.global](https://app.safe.global) (or self-host via
[`safe-smart-account`](https://github.com/safe-global/safe-smart-account))
and deploy one there first. Pick your signer set and threshold (e.g.
3-of-5) with the same care you'd give to choosing the admin key itself,
since this handoff makes that Safe the sole holder of every admin power
these contracts have.

**Does cover:** the on-chain role handoff itself, once a Safe address
exists — `grantRole(DEFAULT_ADMIN_ROLE, safe)` then
`renounceRole(DEFAULT_ADMIN_ROLE, deployerEOA)` on all three contracts,
each step verified on-chain before the next one runs.

## Why this is a two-step handoff, not a one-line ownership swap

OpenZeppelin `AccessControl` has no `transferOwnership`-style helper for
`DEFAULT_ADMIN_ROLE` — it's just a role like any other, granted and
revoked independently. That means the handoff is inherently two calls:
grant the new admin, then have the old admin give up its own role. If
those two calls aren't ordered and verified correctly, there's a real
failure mode: renounce the EOA's role *before* confirming the grant to
the Safe actually landed (e.g. a revert, a stale nonce, an RPC hiccup),
and the contract permanently has **zero** admins — `AccessControl` has no
recovery path for that; not even redeploying can fix an already-deployed
contract stuck in that state.

`script/TransferAdminToMultisig.s.sol` handles this by checking
`hasRole(DEFAULT_ADMIN_ROLE, safe)` on-chain immediately after the grant,
and reverting the whole script before ever calling `renounceRole` if that
check fails. Each of the three contracts goes through this
grant-verify-renounce sequence independently and in order.

## Running it

1. Deploy the Safe (see above). Note its address.
2. Have `contracts/addresses_v2.json` (written by `DeployV2.s.sol` /
   `backend/scripts/write_v2_addresses.py`) or your own record of the
   three deployed V2 contract addresses.
3. Dry run first — no `--broadcast`, so nothing goes on-chain, but the
   script's own `require()` checks still run against simulated state and
   will catch a wrong `SAFE_ADDRESS` or a `PRIVATE_KEY` that isn't
   actually the current admin:

   ```bash
   PRIVATE_KEY=<current admin EOA's key> \
   SAFE_ADDRESS=<deployed Safe address> \
   AUDIT_LOG_ADDRESS=<from addresses_v2.json> \
   TRUST_SCORE_ADDRESS=<from addresses_v2.json> \
   IDENTITY_REGISTRY_ADDRESS=<from addresses_v2.json> \
   forge script script/TransferAdminToMultisig.s.sol --rpc-url <rpc>
   ```

4. If the dry run's log output looks right (check the Safe address is
   what you expect — this is the one step where a typo is expensive),
   re-run with `--broadcast`:

   ```bash
   ... same env vars ... \
   forge script script/TransferAdminToMultisig.s.sol --rpc-url <rpc> --broadcast
   ```

5. Confirm from a fresh terminal/session (not just trusting the script's
   own output) — read `hasRole(0x00, <safe>)` and `hasRole(0x00,
   <old EOA>)` on each of the three contracts via `cast call` or a block
   explorer. The old EOA should show `false` on all three; the Safe
   should show `true`.
6. From here on, pausing a contract, calling `resetRun`, or
   registering/revoking an agent identity requires collecting signatures
   through the Safe's own multisig flow — there is no longer any single
   key that can do those things alone.

## What the anchor worker's key keeps doing

None of this touches `ANCHOR_ROLE` — the anchor worker (and
`agents/scorer.py`'s score writes) keep using their own signing key
(`backend/blockchain/signer.py` — `LocalKeySigner`, or for real
deployments `AwsKmsSigner`/`GcpKmsSigner`/`VaultKvSigner`, selected via
`SIGNER_BACKEND`) for routine `anchorBatch`/`updateScore` calls. Only the
rarely-used admin powers (pause, role management, agent
registration/revocation, `resetRun`) move to the Safe — the hot,
frequently-used write path stays a single automated key by design. Via a
KMS backend (`aws_kms`/`gcp_kms`) that key's raw material never touches
process memory at all — only a digest goes in, a signature comes out.
`vault_kv` is a weaker but still real improvement over a plaintext env
var: the key is fetched from HashiCorp Vault's KV v2 engine at startup
(centrally managed, access-controlled, audit-logged) but does sit in
process memory after that, since Vault OSS's Transit engine — the actual
"never expose the key" equivalent — doesn't support secp256k1. See
`VaultKvSigner`'s own docstring in `backend/blockchain/signer.py`.

## Testing

`test/TransferAdminToMultisig.t.sol` exercises the actual
`TransferAdminToMultisig.transfer()` logic (not a reimplementation of it)
against three real, freshly-deployed V2 contracts on Foundry's local EVM,
asserting: the Safe ends up holding `DEFAULT_ADMIN_ROLE` on all three,
the former EOA holds it on none of them, the former EOA can no longer
call an admin-only function (`pause()`) afterward, and the two guard
`require()`s (`SAFE_ADDRESS` unset / equal to the current admin) revert
before any state changes. A real Gnosis Safe contract isn't deployed in
the test (out of scope, see above) — a plain address stands in for one,
which is a faithful test of the actual mechanism, since
`AccessControl`'s role checks never distinguish an EOA from a contract.
