// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/AgentIdentityRegistryV2.sol";

contract AgentIdentityRegistryV2Test is Test {
    // Redeclared locally (must match AgentIdentityRegistryV2.sol exactly,
    // including indexed-ness) so vm.expectEmit can be used without a
    // qualified emit, which solc 0.8.20 doesn't support for external
    // contract events.
    event IntegrityViolation(
        uint256 indexed projectId, string agentId, bytes32 expectedHash, bytes32 providedHash, uint256 timestamp
    );

    AgentIdentityRegistryV2 registry;

    address admin = address(this);
    address relayer = address(0xBEEF);
    address stranger = address(0xDEAD);

    uint256 constant PROJECT_A = 1;
    uint256 constant PROJECT_B = 2;

    bytes32 hashV1 = keccak256("researcher-config-v1");
    bytes32 hashV2 = keccak256("researcher-config-v2");

    function setUp() public {
        registry = new AgentIdentityRegistryV2(admin);
        registry.grantRole(registry.REGISTRAR_ROLE(), relayer);
    }

    // ── Role separation — registration needs REGISTRAR_ROLE specifically, ──
    // ── not DEFAULT_ADMIN_ROLE and not ANCHOR_ROLE (which doesn't exist ────
    // ── on this contract at all) ─────────────────────────────────────────

    function test_RelayerHoldsOnlyRegistrarRole() public view {
        assertTrue(registry.hasRole(registry.REGISTRAR_ROLE(), relayer));
        assertFalse(registry.hasRole(registry.DEFAULT_ADMIN_ROLE(), relayer));
    }

    function test_RevertWhen_StrangerRegistersAgent() public {
        vm.prank(stranger);
        vm.expectRevert();
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
    }

    function test_RevertWhen_AdminAloneCannotRegister_MustHoldRegistrarRoleToo() public {
        // Admin has DEFAULT_ADMIN_ROLE but was never granted REGISTRAR_ROLE
        // itself — roles are additive, not hierarchical, by design here
        // (same invariant AgentAuditLogV2Test asserts for ANCHOR_ROLE).
        vm.expectRevert();
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
    }

    function test_RegistrarCanRegisterAgent() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
        assertTrue(registry.isRegistered(_key(PROJECT_A, "researcher")));
        assertTrue(registry.verifyAgent(PROJECT_A, "researcher", hashV1));
    }

    function test_RevertWhen_StrangerRevokesAgent() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");

        vm.prank(stranger);
        vm.expectRevert();
        registry.revokeAgent(PROJECT_A, "researcher");
    }

    // ── Re-registration updates in place, doesn't duplicate ─────────────────

    function test_ReRegistering_UpdatesHash() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.registerAgent(PROJECT_A, "researcher", hashV2, "model", "v2");
        vm.stopPrank();

        assertEq(registry.getCodeHash(PROJECT_A, "researcher"), hashV2);
        assertFalse(registry.verifyAgent(PROJECT_A, "researcher", hashV1));
        assertTrue(registry.verifyAgent(PROJECT_A, "researcher", hashV2));
        assertEq(registry.getAgentCount(), 1);
    }

    // ── verifyAgent — substitution detection ────────────────────────────────

    function test_VerifyAgent_FalseOnMismatch() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        assertFalse(registry.verifyAgent(PROJECT_A, "researcher", hashV2));
    }

    function test_VerifyAgent_FalseWhenRevoked() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.revokeAgent(PROJECT_A, "researcher");
        vm.stopPrank();
        assertFalse(registry.verifyAgent(PROJECT_A, "researcher", hashV1));
    }

    function test_VerifyAgent_FalseForUnregistered() public view {
        assertFalse(registry.verifyAgent(PROJECT_A, "ghost", hashV1));
    }

    // ── verifyAgentAndLog — the tamper alarm ────────────────────────────────

    function test_VerifyAgentAndLog_EmitsIntegrityViolationOnMismatch() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");

        // agentId is not indexed (see the contract's event docstring);
        // projectId IS — so topic1 (projectId) is checked, topic2/3 don't
        // exist for this event.
        vm.expectEmit(true, false, false, true);
        emit IntegrityViolation(PROJECT_A, "researcher", hashV1, hashV2, block.timestamp);
        bool ok = registry.verifyAgentAndLog(PROJECT_A, "researcher", hashV2);
        assertFalse(ok);
    }

    // ── verifyAgentFull ──────────────────────────────────────────────────

    function test_VerifyAgentFull_ValidAgent() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result =
            registry.verifyAgentFull(PROJECT_A, "researcher", hashV1);
        assertTrue(result.isValid);
        assertTrue(result.hashMatches);
    }

    function test_VerifyAgentFull_UnregisteredAgent() public view {
        AgentIdentityRegistryV2.VerificationResult memory result = registry.verifyAgentFull(PROJECT_A, "ghost", hashV1);
        assertFalse(result.isValid);
        assertFalse(result.isActive);
        assertFalse(result.hashMatches);
        assertEq(result.agentId, "ghost");
        assertEq(result.providedHash, hashV1);
    }

    function test_VerifyAgentFull_TamperedButStillActive() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result =
            registry.verifyAgentFull(PROJECT_A, "researcher", hashV2);
        assertFalse(result.isValid);
        assertTrue(result.isActive);
        assertFalse(result.hashMatches);
    }

    // ── registerAgent input validation ──────────────────────────────────

    function test_RevertWhen_RegisteringWithEmptyAgentId() public {
        vm.prank(relayer);
        vm.expectRevert("AgentIdentityRegistryV2: agentId cannot be empty");
        registry.registerAgent(PROJECT_A, "", hashV1, "model", "v1");
    }

    function test_RevertWhen_RegisteringWithZeroCodeHash() public {
        vm.prank(relayer);
        vm.expectRevert("AgentIdentityRegistryV2: codeHash cannot be zero");
        registry.registerAgent(PROJECT_A, "researcher", bytes32(0), "model", "v1");
    }

    // ── revokeAgent — double-revoke and the agentExists modifier ────────

    function test_RevertWhen_RevokingAlreadyRevokedAgent() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.revokeAgent(PROJECT_A, "researcher");
        vm.expectRevert("AgentIdentityRegistryV2: agent already revoked");
        registry.revokeAgent(PROJECT_A, "researcher");
        vm.stopPrank();
    }

    // ── getAgent — the other caller of the agentExists modifier, distinct ──
    // ── from revokeAgent's instantiation of the same require ────────────

    function test_RevertWhen_GettingUnregisteredAgent() public {
        vm.expectRevert("AgentIdentityRegistryV2: agent not registered");
        registry.getAgent(PROJECT_A, "ghost");
    }

    function test_GetAgent_ReturnsRecordForRegisteredAgent() public {
        vm.prank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");

        AgentIdentityRegistryV2.AgentRecord memory record = registry.getAgent(PROJECT_A, "researcher");
        assertEq(record.projectId, PROJECT_A);
        assertEq(record.agentId, "researcher");
        assertEq(record.codeHash, hashV1);
        assertTrue(record.isActive);
        assertEq(record.modelName, "llama-3.3-70b-versatile");
    }

    // ── verifyAgentAndLog's early-return branch — distinct from the
    //    hash-mismatch tamper-alarm path already covered above ──────────

    function test_VerifyAgentAndLog_ReturnsFalseForUnregistered() public {
        bool ok = registry.verifyAgentAndLog(PROJECT_A, "ghost", hashV1);
        assertFalse(ok);
    }

    function test_VerifyAgentAndLog_ReturnsFalseWhenRevoked() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.revokeAgent(PROJECT_A, "researcher");
        vm.stopPrank();

        bool ok = registry.verifyAgentAndLog(PROJECT_A, "researcher", hashV1);
        assertFalse(ok);
    }

    // ── Project namespacing (plan §14.2) — the actual point of this pass:
    //    (projectId, agentId) is the composite key, so two tenants can
    //    both register "researcher" without colliding. Before this fix,
    //    every one of these would have failed — the second registerAgent
    //    call would have silently OVERWRITTEN the first tenant's record
    //    (an AgentUpdated, not a fresh AgentRegistered), and the first
    //    tenant's verifyAgent() would start comparing against the SECOND
    //    tenant's hash — a genuine cross-tenant identity-substitution bug,
    //    not a hypothetical. ──────────────────────────────────────────

    function test_TwoProjectsRegisteringTheSameAgentIdDoNotCollide() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.registerAgent(PROJECT_B, "researcher", hashV2, "model", "v2");
        vm.stopPrank();

        // Project A's record is untouched by Project B's registration —
        // this is the exact bug this whole pass fixes.
        assertTrue(registry.verifyAgent(PROJECT_A, "researcher", hashV1));
        assertFalse(registry.verifyAgent(PROJECT_A, "researcher", hashV2));

        assertTrue(registry.verifyAgent(PROJECT_B, "researcher", hashV2));
        assertFalse(registry.verifyAgent(PROJECT_B, "researcher", hashV1));

        assertEq(registry.getAgentCount(), 2);
    }

    function test_RevokingOneProjectsAgentDoesNotAffectAnothers() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "model", "v1");
        registry.registerAgent(PROJECT_B, "researcher", hashV1, "model", "v1");

        registry.revokeAgent(PROJECT_A, "researcher");
        vm.stopPrank();

        assertFalse(registry.verifyAgent(PROJECT_A, "researcher", hashV1));
        // Same agentId, same codeHash, DIFFERENT project — still active.
        assertTrue(registry.verifyAgent(PROJECT_B, "researcher", hashV1));
    }

    function test_GetAgent_ReturnsTheCorrectProjectsRecordNotTheOthers() public {
        vm.startPrank(relayer);
        registry.registerAgent(PROJECT_A, "researcher", hashV1, "modelA", "v1");
        registry.registerAgent(PROJECT_B, "researcher", hashV2, "modelB", "v2");
        vm.stopPrank();

        AgentIdentityRegistryV2.AgentRecord memory recordA = registry.getAgent(PROJECT_A, "researcher");
        AgentIdentityRegistryV2.AgentRecord memory recordB = registry.getAgent(PROJECT_B, "researcher");

        assertEq(recordA.projectId, PROJECT_A);
        assertEq(recordA.codeHash, hashV1);
        assertEq(recordA.modelName, "modelA");

        assertEq(recordB.projectId, PROJECT_B);
        assertEq(recordB.codeHash, hashV2);
        assertEq(recordB.modelName, "modelB");
    }

    function _key(uint256 projectId, string memory agentId) internal pure returns (bytes32) {
        return keccak256(abi.encode(projectId, agentId));
    }
}
