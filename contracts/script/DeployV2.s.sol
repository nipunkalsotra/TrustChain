// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "forge-std/console.sol";

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
/// Local dev / Anvil usage (deployer, admin and relayer are all the same
/// well-known Anvil test account — fine for local testing, NOT how this
/// should be deployed anywhere with real value behind it, where admin
/// should be a multisig and the relayer a KMS-held key with no other
/// authority; see the Phase 2 plan's security section):
///
///   forge script script/DeployV2.s.sol \
///     --rpc-url http://localhost:8545 \
///     --private-key $PRIVATE_KEY \
///     --broadcast
///
/// RELAYER_ADDRESS defaults to the deployer if unset, so a single-key local
/// setup works with zero extra configuration.
contract DeployV2 is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerPrivateKey);
        address relayer = vm.envOr("RELAYER_ADDRESS", deployer);

        console.log("========================================");
        console.log("  TrustChain V2 Deployment");
        console.log("========================================");
        console.log("Deployer (admin):", deployer);
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
    }
}
