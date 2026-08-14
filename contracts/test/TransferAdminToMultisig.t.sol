// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../script/TransferAdminToMultisig.s.sol";
import "../src/v2/AgentAuditLogV2.sol";
import "../src/v2/TrustScoreRegistryV2.sol";
import "../src/v2/AgentIdentityRegistryV2.sol";

/// @notice Runs the ACTUAL TransferAdminToMultisig.transfer() logic (not
/// a reimplementation of it) against three real, freshly-deployed V2
/// contracts, then asserts the end state directly against those
/// contracts. Calls `transfer(...)` directly with explicit parameters
/// rather than going through `run()`'s env-var reads (`vm.setEnv`/
/// `vm.envXXX` are real OS-process environment variables shared across
/// every concurrently-running test in this forge process — using them
/// for per-test config caused genuine, observed nondeterministic
/// cross-test races when this file's tests ran in parallel; see
/// TransferAdminToMultisig.s.sol's docstring for why `run()` stays a thin
/// env-reading wrapper around `transfer()` specifically so tests avoid
/// that).
///
/// A deployed Gnosis Safe isn't spun up here (out of scope — see the
/// script's docstring); `safe` below is a plain address standing in for
/// one, which is a faithful test of the actual mechanism because
/// OpenZeppelin AccessControl's role checks only ever look at an address,
/// never at whether it's an EOA or a contract — nothing about
/// grantRole/renounceRole/hasRole behaves differently for a Safe than for
/// any other address.
contract TransferAdminToMultisigTest is Test {
    AgentAuditLogV2 auditLog;
    TrustScoreRegistryV2 trustScore;
    AgentIdentityRegistryV2 identityRegistry;
    TransferAdminToMultisig transferScript;

    uint256 adminKey = 0xA11CE;
    address admin;
    address safe = address(0x5AFE);

    bytes32 constant DEFAULT_ADMIN_ROLE = bytes32(0);

    function setUp() public {
        admin = vm.addr(adminKey);
        auditLog = new AgentAuditLogV2(admin);
        trustScore = new TrustScoreRegistryV2(admin);
        identityRegistry = new AgentIdentityRegistryV2(admin);
        transferScript = new TransferAdminToMultisig();
    }

    function _targets() internal view returns (address[3] memory) {
        return [address(auditLog), address(trustScore), address(identityRegistry)];
    }

    function _names() internal pure returns (string[3] memory) {
        return ["AgentAuditLogV2", "TrustScoreRegistryV2", "AgentIdentityRegistryV2"];
    }

    function test_TransferHandsAdminToSafeAndStripsEOA() public {
        assertTrue(auditLog.hasRole(DEFAULT_ADMIN_ROLE, admin));
        assertTrue(trustScore.hasRole(DEFAULT_ADMIN_ROLE, admin));
        assertTrue(identityRegistry.hasRole(DEFAULT_ADMIN_ROLE, admin));

        transferScript.transfer(adminKey, safe, _targets(), _names());

        // Safe now holds admin on all three...
        assertTrue(auditLog.hasRole(DEFAULT_ADMIN_ROLE, safe));
        assertTrue(trustScore.hasRole(DEFAULT_ADMIN_ROLE, safe));
        assertTrue(identityRegistry.hasRole(DEFAULT_ADMIN_ROLE, safe));

        // ...and the former EOA admin holds it on none of them.
        assertFalse(auditLog.hasRole(DEFAULT_ADMIN_ROLE, admin));
        assertFalse(trustScore.hasRole(DEFAULT_ADMIN_ROLE, admin));
        assertFalse(identityRegistry.hasRole(DEFAULT_ADMIN_ROLE, admin));
    }

    function test_FormerAdminCanNoLongerPauseAfterTransfer() public {
        transferScript.transfer(adminKey, safe, _targets(), _names());

        vm.prank(admin);
        vm.expectRevert();
        trustScore.pause();

        // The Safe address, however, now can (proving the role transfer
        // is a real, functional handoff, not just a bookkeeping flag).
        vm.prank(safe);
        trustScore.pause();
        assertTrue(trustScore.paused());
    }

    function test_RevertWhen_SafeAddressIsZero() public {
        vm.expectRevert(bytes("SAFE_ADDRESS not set"));
        transferScript.transfer(adminKey, address(0), _targets(), _names());
    }

    function test_RevertWhen_SafeAddressEqualsCurrentAdmin() public {
        vm.expectRevert(bytes("SAFE_ADDRESS must differ from the current admin EOA"));
        transferScript.transfer(adminKey, admin, _targets(), _names());
    }
}
