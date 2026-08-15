// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "@openzeppelin/contracts/access/IAccessControl.sol";

import "../src/v2/AgentAuditLogV2.sol";
import "../src/v2/TrustScoreRegistryV2.sol";
import "../src/v2/AgentIdentityRegistryV2.sol";
import "../src/v2/TrustChainRegistry.sol";

/// @title  DeployV2
/// @notice Deploys the three V2 contracts, grants ANCHOR_ROLE to a
///         relayer address (the anchor worker's signing key), and
///         registers this generation (version 2) in TrustChainRegistry
///         so SDKs/indexers can resolve current addresses without
///         hardcoding them (§12.1/§12.4).
///
/// DEFAULT-DEPLOYMENT POSTURE (evaluated as part of closing the gap
/// between this script's original single-EOA-admin default and Phase
/// 2's own target diagram — a Safe holding DEFAULT_ADMIN_ROLE, not the
/// deployer): optional `SAFE_ADDRESS` env var.
///
/// If set: the deployer briefly holds DEFAULT_ADMIN_ROLE on all four
/// contracts (unavoidable — granting ANCHOR_ROLE/REGISTRAR_ROLE to the
/// relayer requires being DEFAULT_ADMIN_ROLE's holder, since these
/// contracts' constructors only take one `admin` address, not a separate
/// relayer; an earlier version of this script tried making the
/// constructor name the Safe directly and skip the deployer entirely,
/// which turned out to make every subsequent grantRole() call in this
/// SAME script revert with AccessControlUnauthorizedAccount — found by
/// a real `forge test` run, not by inspection), but ONLY for the
/// duration of this one script execution: after granting the relayer
/// its roles, the deployer grants DEFAULT_ADMIN_ROLE to the Safe,
/// VERIFIES it landed, then renounces its own DEFAULT_ADMIN_ROLE on all
/// four contracts before this script returns — the exact same grant-
/// verify-renounce safety sequence
/// docs/multisig-admin-handoff.md's TransferAdminToMultisig.s.sol uses,
/// just folded into ONE atomic script run instead of two separate
/// manual steps. The practical difference from today's default: no
/// human has to remember to run a second script afterward, and there's
/// no arbitrarily-long real-world window (hours, days, a forgotten
/// follow-up) where a single EOA is the sole admin — only the few
/// seconds this script itself takes to run.
///
/// If unset: falls back to the original deployer-EOA-admin behavior,
/// permanently (TransferAdminToMultisig.s.sol remains the correct
/// manual follow-up in that case) — kept as the default specifically
/// because local dev/Anvil has no Safe to point at; a fresh Anvil chain
/// has no persistent multisig infrastructure standing up before this
/// script runs, so requiring SAFE_ADDRESS unconditionally would break
/// the zero-config local workflow every other doc in this repo assumes.
/// A loud console.log() warning fires whenever this fallback path is
/// taken, so it's a visible, remembered choice rather than a silent
/// default that's easy to forget about when deploying somewhere real.
/// See docs/adr/0012-multisig-default-deployment-posture.md for the
/// full evaluation and rationale.
///
/// Local dev / Anvil usage (deployer, admin and relayer are all the same
/// well-known Anvil test account — fine for local testing, NOT how this
/// should be deployed anywhere with real value behind it):
///
///   forge script script/DeployV2.s.sol \
///     --rpc-url http://localhost:8545 \
///     --private-key $PRIVATE_KEY \
///     --broadcast
///
/// Real deployment, admin = an already-deployed Safe from the start:
///
///   SAFE_ADDRESS=<deployed Safe address> \
///   forge script script/DeployV2.s.sol \
///     --rpc-url <rpc> \
///     --private-key $PRIVATE_KEY \
///     --broadcast
///
/// RELAYER_ADDRESS defaults to the deployer if unset, so a single-key local
/// setup works with zero extra configuration. `run()` only reads env vars
/// and delegates to `deploy(...)` — same reasoning as
/// TransferAdminToMultisig.s.sol's own run()/transfer() split: tests call
/// deploy(...) directly with explicit parameters instead of vm.setEnv/
/// vm.envXXX, which are real OS-process environment variables shared
/// across every concurrently-running test in the same forge process.
contract DeployV2 is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);
        address relayer = vm.envOr("RELAYER_ADDRESS", deployer);
        address safe = vm.envOr("SAFE_ADDRESS", address(0));

        deploy(deployerPrivateKey, relayer, safe);
    }

    bytes32 constant DEFAULT_ADMIN_ROLE = bytes32(0);

    function deploy(uint256 deployerPrivateKey, address relayer, address safe)
        public
        returns (address auditLogAddr, address trustScoreAddr, address identityRegistryAddr, address registryAddr)
    {
        address deployer = vm.addr(deployerPrivateKey);

        console.log("========================================");
        console.log("  TrustChain V2 Deployment");
        console.log("========================================");
        console.log("Deployer:", deployer);
        if (safe != address(0)) {
            console.log("Admin (DEFAULT_ADMIN_ROLE, after atomic handoff below):", safe);
        } else {
            console.log("Admin (DEFAULT_ADMIN_ROLE):", deployer);
            console.log("");
            console.log("!! WARNING: SAFE_ADDRESS not set -- admin stays the deployer EOA,");
            console.log("!! not a multisig. Fine for local dev; for any deployment with");
            console.log("!! real value behind it, set SAFE_ADDRESS or run");
            console.log("!! TransferAdminToMultisig.s.sol immediately afterward -- see");
            console.log("!! docs/multisig-admin-handoff.md.");
            console.log("");
        }
        console.log("Relayer (anchor worker):", relayer);
        console.log("Chain ID:", block.chainid);
        console.log("");

        vm.startBroadcast(deployerPrivateKey);

        console.log(">> [1/3] Deploying AgentAuditLogV2...");
        AgentAuditLogV2 auditLog = new AgentAuditLogV2(deployer);
        auditLog.grantRole(auditLog.ANCHOR_ROLE(), relayer);
        console.log("   AgentAuditLogV2 deployed at:", address(auditLog));

        console.log(">> [2/3] Deploying TrustScoreRegistryV2...");
        TrustScoreRegistryV2 trustScore = new TrustScoreRegistryV2(deployer);
        trustScore.grantRole(trustScore.ANCHOR_ROLE(), relayer);
        console.log("   TrustScoreRegistryV2 deployed at:", address(trustScore));

        console.log(">> [3/4] Deploying AgentIdentityRegistryV2...");
        AgentIdentityRegistryV2 identityRegistry = new AgentIdentityRegistryV2(deployer);
        identityRegistry.grantRole(identityRegistry.REGISTRAR_ROLE(), relayer);
        console.log("   AgentIdentityRegistryV2 deployed at:", address(identityRegistry));
        console.log("   relayer granted REGISTRAR_ROLE (distinct from ANCHOR_ROLE -- see contract docstring)");

        console.log(">> [4/4] Deploying TrustChainRegistry...");
        TrustChainRegistry registry = new TrustChainRegistry(deployer);
        registry.registerDeployment(2, address(auditLog), address(trustScore), address(identityRegistry));
        registry.setCurrentVersion(2);
        console.log("   TrustChainRegistry deployed at:", address(registry));
        console.log("   registered as version 2, set as current");

        if (safe != address(0)) {
            console.log("");
            console.log(">> Handing DEFAULT_ADMIN_ROLE to the Safe on all four contracts...");
            _handOffAdminToSafe(deployer, safe, address(auditLog), "AgentAuditLogV2");
            _handOffAdminToSafe(deployer, safe, address(trustScore), "TrustScoreRegistryV2");
            _handOffAdminToSafe(deployer, safe, address(identityRegistry), "AgentIdentityRegistryV2");
            _handOffAdminToSafe(deployer, safe, address(registry), "TrustChainRegistry");
            console.log("   Done -- the deployer EOA holds DEFAULT_ADMIN_ROLE on none of them.");
        }

        vm.stopBroadcast();

        console.log("");
        console.log("========================================");
        console.log("  COPY THIS INTO backend/contracts/addresses_v2.json");
        console.log("========================================");
        console.log("{");
        console.log(string.concat('  "network": "', block.chainid == 31337 ? "anvil_local" : "unknown", '",'));
        console.log(string.concat('  "chainId": ', vm.toString(block.chainid), ","));
        console.log(string.concat('  "AgentAuditLogV2": "', vm.toString(address(auditLog)), '",'));
        console.log(string.concat('  "TrustScoreRegistryV2": "', vm.toString(address(trustScore)), '",'));
        console.log(string.concat('  "AgentIdentityRegistryV2": "', vm.toString(address(identityRegistry)), '",'));
        console.log(string.concat('  "TrustChainRegistry": "', vm.toString(address(registry)), '"'));
        console.log("}");

        return (address(auditLog), address(trustScore), address(identityRegistry), address(registry));
    }

    /// @dev Same grant-verify-renounce sequence as
    ///      TransferAdminToMultisig.s.sol's own transfer() loop — see
    ///      that script's SAFETY note on why the order (and the
    ///      verification in between) matters: renouncing before
    ///      confirming the grant landed would leave the contract with
    ///      zero admins forever, unrecoverable even by redeploying.
    function _handOffAdminToSafe(address deployer, address safe, address target, string memory name) internal {
        IAccessControl(target).grantRole(DEFAULT_ADMIN_ROLE, safe);
        require(
            IAccessControl(target).hasRole(DEFAULT_ADMIN_ROLE, safe),
            string.concat(name, ": grantRole to Safe did not take effect -- aborting before renounce")
        );
        IAccessControl(target).renounceRole(DEFAULT_ADMIN_ROLE, deployer);
        require(
            !IAccessControl(target).hasRole(DEFAULT_ADMIN_ROLE, deployer),
            string.concat(name, ": renounceRole did not take effect")
        );
        console.log("   ", name, "-> Safe (verified)");
    }
}
