// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/TrustChainRegistry.sol";

contract TrustChainRegistryTest is Test {
    event DeploymentRegistered(
        uint256 indexed version, address auditLog, address trustScore, address identityRegistry, uint256 timestamp
    );
    event CurrentVersionUpdated(uint256 indexed previousVersion, uint256 indexed newVersion);

    TrustChainRegistry registry;

    address admin = address(this);
    address stranger = address(0xDEAD);

    address auditLog = address(0x1111);
    address trustScore = address(0x2222);
    address identityRegistry = address(0x3333);

    function setUp() public {
        registry = new TrustChainRegistry(admin);
    }

    function test_RegisterDeployment_StoresAddressesAndEmits() public {
        vm.expectEmit(true, false, false, true);
        emit DeploymentRegistered(2, auditLog, trustScore, identityRegistry, block.timestamp);
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);

        TrustChainRegistry.Deployment memory d = registry.getDeployment(2);
        assertEq(d.auditLog, auditLog);
        assertEq(d.trustScore, trustScore);
        assertEq(d.identityRegistry, identityRegistry);
        assertEq(d.registeredAt, block.timestamp);
        assertTrue(registry.isRegistered(2));
    }

    function test_RevertWhen_RegisteringAnAlreadyRegisteredVersion() public {
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);
        vm.expectRevert(abi.encodeWithSelector(TrustChainRegistry.VersionAlreadyRegistered.selector, 2));
        registry.registerDeployment(2, address(0x4444), address(0x5555), address(0x6666));
    }

    function test_RevertWhen_RegisteringWithAZeroAddress() public {
        vm.expectRevert(TrustChainRegistry.ZeroAddress.selector);
        registry.registerDeployment(2, address(0), trustScore, identityRegistry);
    }

    function test_RevertWhen_StrangerRegistersDeployment() public {
        vm.prank(stranger);
        vm.expectRevert();
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);
    }

    function test_SetCurrentVersion_UpdatesPointerAndEmits() public {
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);

        vm.expectEmit(true, true, false, false);
        emit CurrentVersionUpdated(0, 2);
        registry.setCurrentVersion(2);

        assertEq(registry.currentVersion(), 2);
        TrustChainRegistry.Deployment memory current = registry.getCurrentDeployment();
        assertEq(current.auditLog, auditLog);
    }

    function test_RevertWhen_SettingCurrentVersionToUnregisteredVersion() public {
        vm.expectRevert(abi.encodeWithSelector(TrustChainRegistry.VersionNotRegistered.selector, 99));
        registry.setCurrentVersion(99);
    }

    function test_RevertWhen_StrangerSetsCurrentVersion() public {
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);
        vm.prank(stranger);
        vm.expectRevert();
        registry.setCurrentVersion(2);
    }

    function test_RevertWhen_GettingAnUnregisteredVersion() public {
        vm.expectRevert(abi.encodeWithSelector(TrustChainRegistry.VersionNotRegistered.selector, 7));
        registry.getDeployment(7);
    }

    function test_RevertWhen_GettingCurrentDeploymentBeforeAnyVersionIsSet() public {
        // currentVersion defaults to 0, which is never a valid registered
        // version (registerDeployment starts callers at whatever version
        // number they choose, e.g. 2 for V2 — 0 is deliberately never
        // auto-registered), so this should revert rather than return
        // zeroed-out addresses that look plausible but aren't real.
        vm.expectRevert(abi.encodeWithSelector(TrustChainRegistry.VersionNotRegistered.selector, 0));
        registry.getCurrentDeployment();
    }

    function test_MultipleVersionsCanCoexistAndBothRemainReadable() public {
        registry.registerDeployment(1, address(0xA1), address(0xA2), address(0xA3));
        registry.registerDeployment(2, auditLog, trustScore, identityRegistry);
        registry.setCurrentVersion(2);

        // Historical version 1 stays readable and unchanged even though
        // "current" has moved on — proofs anchored under V1 must never
        // become unresolvable (§12.4).
        TrustChainRegistry.Deployment memory v1 = registry.getDeployment(1);
        assertEq(v1.auditLog, address(0xA1));

        TrustChainRegistry.Deployment memory current = registry.getCurrentDeployment();
        assertEq(current.auditLog, auditLog);
    }
}
