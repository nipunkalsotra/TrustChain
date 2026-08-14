// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "@openzeppelin/contracts/access/IAccessControl.sol";

/// @title  TransferAdminToMultisig
/// @notice Moves DEFAULT_ADMIN_ROLE on all three V2 contracts from the
///         deployer EOA to a Gnosis Safe (or any other address — this
///         only depends on OpenZeppelin AccessControl's role model, which
///         doesn't care whether the holder is an EOA or a contract).
///
/// Deploying a full Gnosis Safe contract suite is out of scope here — this
/// script assumes the Safe already exists (deploy one via
/// https://app.safe.global or `safe-smart-account`'s own tooling first,
/// pick your threshold/owners there) and only handles the on-chain
/// role handoff from DeployV2.s.sol's single EOA admin to that Safe.
///
/// SAFETY: this is a two-step, order-sensitive handoff per contract —
/// grant the Safe the role, VERIFY it landed, only THEN renounce the
/// EOA's own role. Renouncing first (or on a chain that later reorgs the
/// grant out) would strand the contract with zero admins forever —
/// AccessControl has no recovery path for that; nothing (not even the
/// zero address) could ever grant or pause again. Each step here reverts
/// the whole script if verification fails, rather than silently
/// continuing to the next contract in a half-migrated state.
///
/// Usage (after deploying V2 contracts and a Safe):
///
///   PRIVATE_KEY=<current admin EOA's key> \
///   SAFE_ADDRESS=<deployed Safe address> \
///   AUDIT_LOG_ADDRESS=<from addresses_v2.json> \
///   TRUST_SCORE_ADDRESS=<from addresses_v2.json> \
///   IDENTITY_REGISTRY_ADDRESS=<from addresses_v2.json> \
///   forge script script/TransferAdminToMultisig.s.sol \
///     --rpc-url <rpc> --broadcast
///
/// Run `forge script ... ` WITHOUT --broadcast first (a dry run) to see
/// the planned calls before they go on-chain — the two require() checks
/// below run against the simulated state either way, so a dry run still
/// catches "SAFE_ADDRESS is wrong" before it costs real gas.
///
/// `run()` only reads env vars and delegates to `transfer(...)` — the
/// actual on-chain logic takes plain parameters instead of reading env
/// vars itself, specifically so tests can call it directly with explicit
/// values (test/TransferAdminToMultisig.t.sol) instead of going through
/// `vm.setEnv`/`vm.envXXX`, which are real OS-process environment
/// variables shared across every concurrently-running test in the same
/// forge process — fine for a one-shot `forge script` invocation, but a
/// source of nondeterministic cross-test races if a parallel test suite
/// read/wrote them for per-test configuration.
contract TransferAdminToMultisig is Script {
    // OpenZeppelin AccessControl's DEFAULT_ADMIN_ROLE is always bytes32(0)
    // (it is its own admin role, by construction) — not a keccak256 of a
    // name like ANCHOR_ROLE, so there's no `.DEFAULT_ADMIN_ROLE()` getter
    // needed here; this constant IS the getter's return value on every
    // AccessControl contract.
    bytes32 constant DEFAULT_ADMIN_ROLE = bytes32(0);

    function run() external {
        uint256 adminPrivateKey = vm.envUint("PRIVATE_KEY");
        address safe = vm.envAddress("SAFE_ADDRESS");
        address[3] memory targets = [
            vm.envAddress("AUDIT_LOG_ADDRESS"),
            vm.envAddress("TRUST_SCORE_ADDRESS"),
            vm.envAddress("IDENTITY_REGISTRY_ADDRESS")
        ];
        string[3] memory names = ["AgentAuditLogV2", "TrustScoreRegistryV2", "AgentIdentityRegistryV2"];

        transfer(adminPrivateKey, safe, targets, names);
    }

    function transfer(uint256 adminPrivateKey, address safe, address[3] memory targets, string[3] memory names) public {
        address currentAdmin = vm.addr(adminPrivateKey);
        require(safe != address(0), "SAFE_ADDRESS not set");
        require(safe != currentAdmin, "SAFE_ADDRESS must differ from the current admin EOA");

        console.log("========================================");
        console.log("  TrustChain V2 -> Multisig Admin Handoff");
        console.log("========================================");
        console.log("Current admin (EOA):", currentAdmin);
        console.log("New admin (Safe):   ", safe);
        console.log("");

        vm.startBroadcast(adminPrivateKey);

        for (uint256 i = 0; i < targets.length; i++) {
            IAccessControl target = IAccessControl(targets[i]);
            console.log(">>", names[i], "at", targets[i]);

            require(
                target.hasRole(DEFAULT_ADMIN_ROLE, currentAdmin),
                string.concat(names[i], ": PRIVATE_KEY's address does not currently hold DEFAULT_ADMIN_ROLE")
            );

            target.grantRole(DEFAULT_ADMIN_ROLE, safe);
            require(
                target.hasRole(DEFAULT_ADMIN_ROLE, safe),
                string.concat(names[i], ": grantRole to Safe did not take effect -- aborting before renounce")
            );
            console.log("   granted DEFAULT_ADMIN_ROLE to Safe, verified on-chain");

            target.renounceRole(DEFAULT_ADMIN_ROLE, currentAdmin);
            require(
                !target.hasRole(DEFAULT_ADMIN_ROLE, currentAdmin),
                string.concat(names[i], ": renounceRole did not take effect")
            );
            console.log("   renounced EOA's own DEFAULT_ADMIN_ROLE, verified on-chain");
        }

        vm.stopBroadcast();

        console.log("");
        console.log("Done. All three V2 contracts are now administered solely by the Safe.");
        console.log("The former EOA admin retains no special role on any of them.");
    }
}
