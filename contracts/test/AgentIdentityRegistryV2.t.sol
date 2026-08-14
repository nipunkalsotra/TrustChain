// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/AgentIdentityRegistryV2.sol";

contract AgentIdentityRegistryV2Test is Test {
    // Redeclared locally (must match AgentIdentityRegistryV2.sol exactly,
    // including indexed-ness) so vm.expectEmit can be used without a
    // qualified emit, which solc 0.8.20 doesn't support for external
    // contract events.
    event IntegrityViolation(string agentId, bytes32 expectedHash, bytes32 providedHash, uint256 timestamp);

    AgentIdentityRegistryV2 registry;

    address admin = address(this);
    address relayer = address(0xBEEF);
    address stranger = address(0xDEAD);

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
        registry.registerAgent("researcher", hashV1, "model", "v1");
    }

    function test_RevertWhen_AdminAloneCannotRegister_MustHoldRegistrarRoleToo() public {
        // Admin has DEFAULT_ADMIN_ROLE but was never granted REGISTRAR_ROLE
        // itself — roles are additive, not hierarchical, by design here
        // (same invariant AgentAuditLogV2Test asserts for ANCHOR_ROLE).
        vm.expectRevert();
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
    }

    function test_RegistrarCanRegisterAgent() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
        assertTrue(registry.isRegistered("researcher"));
        assertTrue(registry.verifyAgent("researcher", hashV1));
    }

    function test_RevertWhen_StrangerRevokesAgent() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");

        vm.prank(stranger);
        vm.expectRevert();
        registry.revokeAgent("researcher");
    }

    // ── Re-registration updates in place, doesn't duplicate ─────────────────

    function test_ReRegistering_UpdatesHash() public {
        vm.startPrank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.registerAgent("researcher", hashV2, "model", "v2");
        vm.stopPrank();

        assertEq(registry.getCodeHash("researcher"), hashV2);
        assertFalse(registry.verifyAgent("researcher", hashV1));
        assertTrue(registry.verifyAgent("researcher", hashV2));
        assertEq(registry.getAgentCount(), 1);
    }

    // ── verifyAgent — substitution detection ────────────────────────────────

    function test_VerifyAgent_FalseOnMismatch() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");
        assertFalse(registry.verifyAgent("researcher", hashV2));
    }

    function test_VerifyAgent_FalseWhenRevoked() public {
        vm.startPrank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.revokeAgent("researcher");
        vm.stopPrank();
        assertFalse(registry.verifyAgent("researcher", hashV1));
    }

    function test_VerifyAgent_FalseForUnregistered() public view {
        assertFalse(registry.verifyAgent("ghost", hashV1));
    }

    // ── verifyAgentAndLog — the tamper alarm ────────────────────────────────

    function test_VerifyAgentAndLog_EmitsIntegrityViolationOnMismatch() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");

        // agentId is no longer indexed (see the contract's event
        // docstring) — no topics left to check besides the event
        // signature itself, so all three topic flags are false now.
        vm.expectEmit(false, false, false, true);
        emit IntegrityViolation("researcher", hashV1, hashV2, block.timestamp);
        bool ok = registry.verifyAgentAndLog("researcher", hashV2);
        assertFalse(ok);
    }

    // ── verifyAgentFull ──────────────────────────────────────────────────

    function test_VerifyAgentFull_ValidAgent() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV1);
        assertTrue(result.isValid);
        assertTrue(result.hashMatches);
    }

    function test_VerifyAgentFull_TamperedButStillActive() public {
        vm.prank(relayer);
        registry.registerAgent("researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV2);
        assertFalse(result.isValid);
        assertTrue(result.isActive);
        assertFalse(result.hashMatches);
    }
}
