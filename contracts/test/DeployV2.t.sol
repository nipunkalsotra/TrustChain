// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../script/DeployV2.s.sol";
import "../src/v2/AgentAuditLogV2.sol";
import "../src/v2/TrustScoreRegistryV2.sol";
import "../src/v2/AgentIdentityRegistryV2.sol";
import "../src/v2/TrustChainRegistry.sol";

/// @notice Runs the ACTUAL DeployV2.deploy() logic (not a
///         reimplementation of it), covering both default-deployment
///         postures evaluated in
///         docs/adr/0012-multisig-default-deployment-posture.md: admin =
///         deployer EOA (SAFE_ADDRESS unset, local-dev default) and
///         admin = a Safe address directly (SAFE_ADDRESS set, the real-
///         deployment path this task added). Calls `deploy(...)`
///         directly with explicit parameters rather than through
///         `run()`'s env-var reads — same reasoning as
///         TransferAdminToMultisigTest (vm.setEnv/vm.envXXX are real
///         OS-process environment variables shared across every
///         concurrently-running test in this forge process).
contract DeployV2Test is Test {
    DeployV2 deployScript;
    uint256 deployerKey = 0xD00D;
    address deployer;
    address relayer = address(0xBEEF);
    address safe = address(0x5AFE);

    bytes32 constant DEFAULT_ADMIN_ROLE = bytes32(0);

    function setUp() public {
        deployer = vm.addr(deployerKey);
        vm.deal(deployer, 100 ether);
        deployScript = new DeployV2();
    }

    function test_DeployWithoutSafeAddress_AdminIsDeployerEOA() public {
        (address auditLog, address trustScore, address identityRegistry, address registry) =
            deployScript.deploy(deployerKey, relayer, address(0));

        assertTrue(AgentAuditLogV2(auditLog).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertTrue(TrustScoreRegistryV2(trustScore).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertTrue(AgentIdentityRegistryV2(identityRegistry).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertTrue(TrustChainRegistry(registry).hasRole(DEFAULT_ADMIN_ROLE, deployer));
    }

    function test_DeployWithSafeAddress_AdminIsSafeDirectly() public {
        (address auditLog, address trustScore, address identityRegistry, address registry) =
            deployScript.deploy(deployerKey, relayer, safe);

        assertTrue(AgentAuditLogV2(auditLog).hasRole(DEFAULT_ADMIN_ROLE, safe));
        assertTrue(TrustScoreRegistryV2(trustScore).hasRole(DEFAULT_ADMIN_ROLE, safe));
        assertTrue(AgentIdentityRegistryV2(identityRegistry).hasRole(DEFAULT_ADMIN_ROLE, safe));
        assertTrue(TrustChainRegistry(registry).hasRole(DEFAULT_ADMIN_ROLE, safe));
    }

    function test_DeployWithSafeAddress_DeployerNeverHoldsAdminOnAnyContract() public {
        // The whole point of this deployment path: no window, however
        // brief, where the deployer EOA holds DEFAULT_ADMIN_ROLE on
        // anything — unlike TransferAdminToMultisig.s.sol's grant-then-
        // renounce dance, the constructor here names the Safe directly.
        (address auditLog, address trustScore, address identityRegistry, address registry) =
            deployScript.deploy(deployerKey, relayer, safe);

        assertFalse(AgentAuditLogV2(auditLog).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertFalse(TrustScoreRegistryV2(trustScore).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertFalse(AgentIdentityRegistryV2(identityRegistry).hasRole(DEFAULT_ADMIN_ROLE, deployer));
        assertFalse(TrustChainRegistry(registry).hasRole(DEFAULT_ADMIN_ROLE, deployer));
    }

    function test_DeployWithSafeAddress_RelayerStillGetsOnlyItsNarrowRoles() public {
        // Confirms the admin-posture change doesn't accidentally widen
        // (or narrow) the relayer's own privileges — it should hold
        // exactly ANCHOR_ROLE/REGISTRAR_ROLE, never DEFAULT_ADMIN_ROLE,
        // regardless of who the admin is.
        (address auditLog, address trustScore, address identityRegistry,) =
            deployScript.deploy(deployerKey, relayer, safe);

        assertTrue(AgentAuditLogV2(auditLog).hasRole(AgentAuditLogV2(auditLog).ANCHOR_ROLE(), relayer));
        assertTrue(TrustScoreRegistryV2(trustScore).hasRole(TrustScoreRegistryV2(trustScore).ANCHOR_ROLE(), relayer));
        assertTrue(
            AgentIdentityRegistryV2(identityRegistry)
                .hasRole(AgentIdentityRegistryV2(identityRegistry).REGISTRAR_ROLE(), relayer)
        );
        assertFalse(AgentAuditLogV2(auditLog).hasRole(DEFAULT_ADMIN_ROLE, relayer));
    }
}
