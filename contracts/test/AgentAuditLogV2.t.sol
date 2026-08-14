// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/AgentAuditLogV2.sol";

contract AgentAuditLogV2Test is Test {
    event BatchAnchored(
        uint256 indexed anchorId,
        bytes32 indexed runIdHash,
        bytes32 merkleRoot,
        uint32 stepCount,
        uint256 timestamp,
        address anchoredBy
    );

    AgentAuditLogV2 auditLog;

    address admin = address(this);
    address relayer = address(0xBEEF);
    address stranger = address(0xDEAD);

    bytes32 runIdHash = keccak256("run_1");

    function setUp() public {
        auditLog = new AgentAuditLogV2(admin);
        auditLog.grantRole(auditLog.ANCHOR_ROLE(), relayer);
    }

    // ── Role separation — the actual "blast radius" claim ──────────────────

    function test_AdminHoldsDefaultAdminRole() public view {
        assertTrue(auditLog.hasRole(auditLog.DEFAULT_ADMIN_ROLE(), admin));
    }

    function test_RelayerHoldsOnlyAnchorRole() public view {
        assertTrue(auditLog.hasRole(auditLog.ANCHOR_ROLE(), relayer));
        assertFalse(auditLog.hasRole(auditLog.DEFAULT_ADMIN_ROLE(), relayer));
    }

    function test_RevertWhen_RelayerTriesToPause() public {
        vm.prank(relayer);
        vm.expectRevert();
        auditLog.pause();
    }

    function test_RevertWhen_RelayerTriesToGrantRoles() public {
        // ANCHOR_ROLE() is itself an external call — evaluating it as an
        // inline argument would consume vm.prank's single-next-call scope
        // before the real grantRole() call happens, making the assertion
        // below pass for the wrong reason (or not at all). Resolve it
        // first, on a separate, unpranked line.
        bytes32 anchorRole = auditLog.ANCHOR_ROLE();

        vm.prank(relayer);
        vm.expectRevert();
        auditLog.grantRole(anchorRole, stranger);
    }

    function test_RevertWhen_StrangerAnchors() public {
        vm.prank(stranger);
        vm.expectRevert();
        auditLog.anchorBatch(runIdHash, keccak256("root"), 5);
    }

    function test_AdminAloneCannotAnchor_MustHoldAnchorRoleToo() public {
        // Admin has DEFAULT_ADMIN_ROLE but was never granted ANCHOR_ROLE —
        // roles are additive, not hierarchical, by design here.
        vm.expectRevert();
        auditLog.anchorBatch(runIdHash, keccak256("root"), 5);
    }

    // ── anchorBatch happy path ──────────────────────────────────────────────

    function test_AnchorBatch_StoresRecord() public {
        bytes32 root = keccak256("merkle-root-1");

        vm.prank(relayer);
        uint256 anchorId = auditLog.anchorBatch(runIdHash, root, 7);

        assertEq(anchorId, 0);
        AgentAuditLogV2.BatchRecord memory rec = auditLog.getBatch(0);
        assertEq(rec.runIdHash, runIdHash);
        assertEq(rec.merkleRoot, root);
        assertEq(rec.stepCount, 7);
        assertEq(rec.anchoredBy, relayer);
        assertEq(rec.timestamp, block.timestamp);
    }

    function test_AnchorBatch_EmitsBatchAnchored() public {
        bytes32 root = keccak256("merkle-root-2");

        vm.expectEmit(true, true, false, true);
        emit BatchAnchored(0, runIdHash, root, 3, block.timestamp, relayer);

        vm.prank(relayer);
        auditLog.anchorBatch(runIdHash, root, 3);
    }

    function test_AnchorBatch_SequentialIds() public {
        vm.startPrank(relayer);
        uint256 id0 = auditLog.anchorBatch(runIdHash, keccak256("r0"), 1);
        uint256 id1 = auditLog.anchorBatch(runIdHash, keccak256("r1"), 1);
        vm.stopPrank();

        assertEq(id0, 0);
        assertEq(id1, 1);
        assertEq(auditLog.getTotalBatches(), 2);
    }

    function test_AnchorBatch_IndexesByRun() public {
        bytes32 otherRun = keccak256("run_2");

        vm.startPrank(relayer);
        auditLog.anchorBatch(runIdHash, keccak256("r0"), 1);
        auditLog.anchorBatch(otherRun, keccak256("r1"), 1);
        auditLog.anchorBatch(runIdHash, keccak256("r2"), 1);
        vm.stopPrank();

        uint256[] memory forRun1 = auditLog.getBatchesForRun(runIdHash);
        assertEq(forRun1.length, 2);
        assertEq(forRun1[0], 0);
        assertEq(forRun1[1], 2);

        uint256[] memory forRun2 = auditLog.getBatchesForRun(otherRun);
        assertEq(forRun2.length, 1);
        assertEq(forRun2[0], 1);
    }

    // ── Validation ────────────────────────────────────────────────────────

    function test_RevertWhen_EmptyBatch() public {
        vm.prank(relayer);
        vm.expectRevert(AgentAuditLogV2.EmptyBatch.selector);
        auditLog.anchorBatch(runIdHash, keccak256("root"), 0);
    }

    function test_RevertWhen_DuplicateRoot() public {
        bytes32 root = keccak256("same-root");

        vm.startPrank(relayer);
        auditLog.anchorBatch(runIdHash, root, 5);

        vm.expectRevert(abi.encodeWithSelector(AgentAuditLogV2.DuplicateRoot.selector, root));
        auditLog.anchorBatch(runIdHash, root, 5);
        vm.stopPrank();
    }

    // ── Pausable ──────────────────────────────────────────────────────────

    function test_RevertWhen_AnchoringWhilePaused() public {
        auditLog.pause();

        vm.prank(relayer);
        vm.expectRevert();
        auditLog.anchorBatch(runIdHash, keccak256("root"), 1);
    }

    function test_AnchoringResumesAfterUnpause() public {
        auditLog.pause();
        auditLog.unpause();

        vm.prank(relayer);
        uint256 anchorId = auditLog.anchorBatch(runIdHash, keccak256("root"), 1);
        assertEq(anchorId, 0);
    }

    // ── verifyProof — real end-to-end Merkle verification through the ──────
    // ── deployed contract, using a hand-built 4-leaf tree ───────────────────

    function test_VerifyProof_RealTree() public {
        bytes32 leaf0 = keccak256("leaf0");
        bytes32 leaf1 = keccak256("leaf1");
        bytes32 leaf2 = keccak256("leaf2");
        bytes32 leaf3 = keccak256("leaf3");

        bytes32 node01 = _hashPair(leaf0, leaf1);
        bytes32 node23 = _hashPair(leaf2, leaf3);
        bytes32 root = _hashPair(node01, node23);

        vm.prank(relayer);
        uint256 anchorId = auditLog.anchorBatch(runIdHash, root, 4);

        bytes32[] memory proof0 = new bytes32[](2);
        proof0[0] = leaf1;
        proof0[1] = node23;
        assertTrue(auditLog.verifyProof(anchorId, leaf0, proof0));

        bytes32[] memory proof2 = new bytes32[](2);
        proof2[0] = leaf3;
        proof2[1] = node01;
        assertTrue(auditLog.verifyProof(anchorId, leaf2, proof2));

        // Wrong leaf with a valid-looking proof must fail.
        assertFalse(auditLog.verifyProof(anchorId, keccak256("not-a-real-leaf"), proof0));
    }

    function _hashPair(bytes32 a, bytes32 b) internal pure returns (bytes32) {
        return a < b ? keccak256(abi.encodePacked(a, b)) : keccak256(abi.encodePacked(b, a));
    }
}
