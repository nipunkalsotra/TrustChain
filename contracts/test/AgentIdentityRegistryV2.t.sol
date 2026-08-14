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
        // No ANCHOR_ROLE exists on this contract at all — registration is
        // DEFAULT_ADMIN_ROLE-only, full stop. `relayer` below is just an
        // arbitrary non-admin address, standing in for "anyone who isn't
        // the multisig," anchor worker included.
    }

    // ── The core claim: only DEFAULT_ADMIN_ROLE can register/revoke ─────────
    // ── identities — NOT the anchor worker's ANCHOR_ROLE. A compromised ────
    // ── hot key cannot fabricate or destroy an agent's identity. ────────────

    function test_RevertWhen_RelayerRegistersAgent() public {
        vm.prank(relayer);
        vm.expectRevert();
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
    }

    function test_RevertWhen_StrangerRegistersAgent() public {
        vm.prank(stranger);
        vm.expectRevert();
        registry.registerAgent("researcher", hashV1, "model", "v1");
    }

    function test_AdminCanRegisterAgent() public {
        registry.registerAgent("researcher", hashV1, "llama-3.3-70b-versatile", "groq-v1");
        assertTrue(registry.isRegistered("researcher"));
        assertTrue(registry.verifyAgent("researcher", hashV1));
    }

    function test_RevertWhen_RelayerRevokesAgent() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");

        vm.prank(relayer);
        vm.expectRevert();
        registry.revokeAgent("researcher");
    }

    // ── Re-registration updates in place, doesn't duplicate ─────────────────

    function test_ReRegistering_UpdatesHash() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.registerAgent("researcher", hashV2, "model", "v2");

        assertEq(registry.getCodeHash("researcher"), hashV2);
        assertFalse(registry.verifyAgent("researcher", hashV1));
        assertTrue(registry.verifyAgent("researcher", hashV2));
        assertEq(registry.getAgentCount(), 1);
    }

    // ── verifyAgent — substitution detection ────────────────────────────────

    function test_VerifyAgent_FalseOnMismatch() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        assertFalse(registry.verifyAgent("researcher", hashV2));
    }

    function test_VerifyAgent_FalseWhenRevoked() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        registry.revokeAgent("researcher");
        assertFalse(registry.verifyAgent("researcher", hashV1));
    }

    function test_VerifyAgent_FalseForUnregistered() public view {
        assertFalse(registry.verifyAgent("ghost", hashV1));
    }

    // ── verifyAgentAndLog — the tamper alarm ────────────────────────────────

    function test_VerifyAgentAndLog_EmitsIntegrityViolationOnMismatch() public {
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
        registry.registerAgent("researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV1);
        assertTrue(result.isValid);
        assertTrue(result.hashMatches);
    }

    function test_VerifyAgentFull_TamperedButStillActive() public {
        registry.registerAgent("researcher", hashV1, "model", "v1");
        AgentIdentityRegistryV2.VerificationResult memory result = registry.verifyAgentFull("researcher", hashV2);
        assertFalse(result.isValid);
        assertTrue(result.isActive);
        assertFalse(result.hashMatches);
    }
}
