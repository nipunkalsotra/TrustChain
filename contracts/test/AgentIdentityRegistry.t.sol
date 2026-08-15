// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/AgentIdentityRegistry.sol";

contract AgentIdentityRegistryTest is Test {
    // Redeclared locally (must match AgentIdentityRegistry.sol exactly,
    // including indexed-ness) so vm.expectEmit can be used without a
    // qualified emit, which solc 0.8.20 doesn't support for external
    // contract events.
    event AgentRegistered(string indexed agentId, bytes32 codeHash, address indexed registeredBy, uint256 timestamp);
    event AgentUpdated(string indexed agentId, bytes32 oldCodeHash, bytes32 newCodeHash, uint256 timestamp);
    event AgentRevoked(string indexed agentId, address indexed revokedBy, uint256 timestamp);
    event IntegrityViolation(string indexed agentId, bytes32 expectedHash, bytes32 providedHash, uint256 timestamp);

    AgentIdentityRegistry registry;

    address owner = address(this);
    address stranger = address(0xBEEF);

    bytes32 hashV1 = keccak256("researcher-config-v1");
    bytes32 hashV2 = keccak256("researcher-config-v2");

    function setUp() public {
        registry = new AgentIdentityRegistry();
    }

    // ── constructor / ownership ──────────────────────────────────────────

    function test_OwnerIsDeployer() public view {
        assertEq(registry.owner(), owner);
    }

    function test_RevertWhen_NonOwnerRegisters() public {
        vm.prank(stranger);
        vm.expectRevert("AgentIdentityRegistry: caller is not the owner");
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
    }

    // ── input validation ─────────────────────────────────────────────────

    function test_RevertWhen_EmptyAgentId() public {
        vm.expectRevert("AgentIdentityRegistry: agentId cannot be empty");
        registry.registerAgent("", hashV1, "model", "v1");
    }

    function test_RevertWhen_ZeroCodeHash() public {
        vm.expectRevert("AgentIdentityRegistry: codeHash cannot be zero");
        registry.registerAgent("researcher", bytes32(0), "model", "v1");
    }

    // ── registration ──────────────────────────────────────────────────────

    function test_RegisterAgent_NewAgentIsActiveAndVerifiable() public {
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");

        assertTrue(registry.isRegistered("researcher"));
        assertTrue(registry.verifyAgent("researcher", hashV1));
        assertEq(registry.getAgentCount(), 1);
        assertEq(registry.getCodeHash("researcher"), hashV1);
    }

    function test_RegisterAgent_EmitsAgentRegisteredOnFirstCall() public {
        vm.expectEmit(true, true, false, true);
        emit AgentRegistered("researcher", hashV1, owner, block.timestamp);
        registry.registerAgent("researcher", hashV1, "model", "v1");
    }

    function test_RegisterAgent_ReRegisteringUpdatesHashAndEmitsAgentUpdated() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        vm.expectEmit(true, false, false, true);
        emit AgentUpdated("researcher", hashV1, hashV2, block.timestamp);
        registry.registerAgent("researcher", hashV2, "model", "v2");

        assertEq(registry.getCodeHash("researcher"), hashV2);
        assertFalse(registry.verifyAgent("researcher", hashV1));
        assertTrue(registry.verifyAgent("researcher", hashV2));
    }

    function test_RegisterAgent_ReRegisteringDoesNotDuplicateAgentList() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.registerAgent("researcher", hashV2, "model", "v2");

        assertEq(registry.getAgentCount(), 1);
    }

    function test_RegisterAgent_MultipleDistinctAgents() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.registerAgent("validator", hashV2, "model", "v1");

        assertEq(registry.getAgentCount(), 2);
    }

    // ── verifyAgent — the substitution detector ────────────────────────────

    function test_VerifyAgent_FalseForUnregisteredAgent() public view {
        assertFalse(registry.verifyAgent("ghost", hashV1));
    }

    function test_VerifyAgent_FalseOnHashMismatch() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        assertFalse(registry.verifyAgent("researcher", hashV2));
    }

    function test_VerifyAgent_FalseWhenRevoked() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.revokeAgent("researcher");
        assertFalse(registry.verifyAgent("researcher", hashV1));
    }

    // ── verifyAgentAndLog — tamper alarm ───────────────────────────────────

    function test_VerifyAgentAndLog_EmitsIntegrityViolationOnMismatch() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        vm.expectEmit(true, false, false, true);
        emit IntegrityViolation("researcher", hashV1, hashV2, block.timestamp);
        bool ok = registry.verifyAgentAndLog("researcher", hashV2);
        assertFalse(ok);
    }

    function test_VerifyAgentAndLog_NoEventOnMatch() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        bool ok = registry.verifyAgentAndLog("researcher", hashV1);
        assertTrue(ok);
    }

    function test_VerifyAgentAndLog_ReturnsFalseForUnregistered() public {
        bool ok = registry.verifyAgentAndLog("ghost", hashV1);
        assertFalse(ok);
    }

    function test_VerifyAgentAndLog_ReturnsFalseWhenRevoked() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.revokeAgent("researcher");
        bool ok = registry.verifyAgentAndLog("researcher", hashV1);
        assertFalse(ok);
    }

    // ── verifyAgentFull — UI panel data ────────────────────────────────────

    function test_VerifyAgentFull_UnregisteredAgent() public view {
        AgentIdentityRegistry.VerificationResult memory result = registry.verifyAgentFull("ghost", hashV1);
        assertFalse(result.isValid);
        assertFalse(result.isActive);
        assertFalse(result.hashMatches);
    }

    function test_VerifyAgentFull_ValidRegisteredAgent() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        AgentIdentityRegistry.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV1);
        assertTrue(result.isValid);
        assertTrue(result.isActive);
        assertTrue(result.hashMatches);
        assertEq(result.storedHash, hashV1);
        assertEq(result.providedHash, hashV1);
    }

    function test_VerifyAgentFull_TamperedHashIsInvalidButStillActive() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        AgentIdentityRegistry.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV2);
        assertFalse(result.isValid);
        assertTrue(result.isActive);
        assertFalse(result.hashMatches);
    }

    // ── revocation ────────────────────────────────────────────────────────

    function test_RevertWhen_RevokingUnregisteredAgent() public {
        vm.expectRevert("AgentIdentityRegistry: agent not registered");
        registry.revokeAgent("ghost");
    }

    function test_RevertWhen_NonOwnerRevokes() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        vm.prank(stranger);
        vm.expectRevert("AgentIdentityRegistry: caller is not the owner");
        registry.revokeAgent("researcher");
    }

    function test_RevertWhen_RevokingAlreadyRevokedAgent() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.revokeAgent("researcher");

        vm.expectRevert("AgentIdentityRegistry: agent already revoked");
        registry.revokeAgent("researcher");
    }

    function test_RevokeAgent_EmitsAgentRevoked() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        vm.expectEmit(true, true, false, true);
        emit AgentRevoked("researcher", owner, block.timestamp);
        registry.revokeAgent("researcher");
    }

    // ── getAgent ──────────────────────────────────────────────────────────

    function test_RevertWhen_GettingUnregisteredAgent() public {
        vm.expectRevert("AgentIdentityRegistry: agent not registered");
        registry.getAgent("ghost");
    }

    function test_GetAgent_ReturnsFullRecord() public {
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");

        AgentIdentityRegistry.AgentRecord memory rec = registry.getAgent("researcher");
        assertEq(rec.agentId, "researcher");
        assertEq(rec.codeHash, hashV1);
        assertEq(rec.registeredBy, owner);
        assertTrue(rec.isActive);
        assertEq(rec.modelName, "llama-3.3-70b-versatile");
        assertEq(rec.modelVersion, "groq-v1");
    }
}
