// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/AgentAuditLog.sol";

contract AgentAuditLogTest is Test {
    // Redeclared locally (must match AgentAuditLog.sol exactly, including
    // indexed-ness) so vm.expectEmit can be used without a qualified emit,
    // which solc 0.8.20 doesn't support for external contract events.
    event ActionLogged(
        string indexed runId,
        string indexed agentId,
        string action,
        uint256 stepIndex,
        bytes32 inputHash,
        bytes32 outputHash,
        uint256 timestamp,
        uint256 recordIndex
    );

    AgentAuditLog auditLog;

    address owner = address(this);
    address stranger = address(0xBEEF);

    bytes32 inputHash = keccak256("input");
    bytes32 outputHash = keccak256("output");

    function setUp() public {
        auditLog = new AgentAuditLog();
    }

    // ── constructor / ownership ──────────────────────────────────────────

    function test_OwnerIsDeployer() public view {
        assertEq(auditLog.owner(), owner);
    }

    function test_RevertWhen_NonOwnerLogsAction() public {
        vm.prank(stranger);
        vm.expectRevert(AgentAuditLog.OnlyOwner.selector);
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
    }

    // ── input validation ─────────────────────────────────────────────────

    function test_RevertWhen_EmptyRunId() public {
        vm.expectRevert(AgentAuditLog.EmptyRunId.selector);
        auditLog.logAction("", "researcher", "SEARCH", inputHash, outputHash, 0, "");
    }

    function test_RevertWhen_EmptyAgentId() public {
        vm.expectRevert(AgentAuditLog.EmptyAgentId.selector);
        auditLog.logAction("run1", "", "SEARCH", inputHash, outputHash, 0, "");
    }

    function test_RevertWhen_EmptyAction() public {
        vm.expectRevert(AgentAuditLog.InvalidAction.selector);
        auditLog.logAction("run1", "researcher", "", inputHash, outputHash, 0, "");
    }

    // ── logAction happy path ─────────────────────────────────────────────

    function test_LogAction_ReturnsSequentialIndices() public {
        uint256 idx0 = auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        uint256 idx1 = auditLog.logAction("run1", "validator", "FACTCHECK", inputHash, outputHash, 1, "");

        assertEq(idx0, 0);
        assertEq(idx1, 1);
        assertEq(auditLog.getTotalRecords(), 2);
    }

    function test_LogAction_StoresRecordFieldsCorrectly() public {
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 3, "meta");

        AgentAuditLog.ActionRecord memory rec = auditLog.getRecord(0);
        assertEq(rec.runId, "run1");
        assertEq(rec.agentId, "researcher");
        assertEq(rec.action, "SEARCH");
        assertEq(rec.inputHash, inputHash);
        assertEq(rec.outputHash, outputHash);
        assertEq(rec.stepIndex, 3);
        assertEq(rec.metadata, "meta");
        assertEq(rec.txSender, owner);
        assertEq(rec.timestamp, block.timestamp);
    }

    function test_LogAction_EmitsActionLogged() public {
        vm.expectEmit(true, true, false, true);
        emit ActionLogged("run1", "researcher", "SEARCH", 0, inputHash, outputHash, block.timestamp, 0);
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
    }

    function test_LogAction_TracksAgentActionCount() public {
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        auditLog.logAction("run2", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        auditLog.logAction("run1", "validator", "FACTCHECK", inputHash, outputHash, 1, "");

        assertEq(auditLog.agentActionCount("researcher"), 2);
        assertEq(auditLog.agentActionCount("validator"), 1);
    }

    // ── run indexing ─────────────────────────────────────────────────────

    function test_GetRunRecordIndices_GroupsByRun() public {
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        auditLog.logAction("run2", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        auditLog.logAction("run1", "validator", "FACTCHECK", inputHash, outputHash, 1, "");

        uint256[] memory run1Indices = auditLog.getRunRecordIndices("run1");
        uint256[] memory run2Indices = auditLog.getRunRecordIndices("run2");

        assertEq(run1Indices.length, 2);
        assertEq(run1Indices[0], 0);
        assertEq(run1Indices[1], 2);

        assertEq(run2Indices.length, 1);
        assertEq(run2Indices[0], 1);
    }

    function test_GetRunRecordIndices_EmptyForUnknownRun() public view {
        uint256[] memory indices = auditLog.getRunRecordIndices("no-such-run");
        assertEq(indices.length, 0);
    }

    function test_GetRecordsBatch_ReturnsInOrder() public {
        auditLog.logAction("run1", "researcher", "SEARCH", inputHash, outputHash, 0, "");
        auditLog.logAction("run1", "validator", "FACTCHECK", inputHash, outputHash, 1, "");

        uint256[] memory indices = new uint256[](2);
        indices[0] = 0;
        indices[1] = 1;

        AgentAuditLog.ActionRecord[] memory batch = auditLog.getRecordsBatch(indices);
        assertEq(batch.length, 2);
        assertEq(batch[0].agentId, "researcher");
        assertEq(batch[1].agentId, "validator");
    }

    // ── verifyRecord ─────────────────────────────────────────────────────

    function test_VerifyRecord_MatchesOriginalInputOutput() public {
        auditLog.logAction(
            "run1", "researcher", "SEARCH", keccak256(bytes("raw input")), keccak256(bytes("raw output")), 0, ""
        );

        (bool inputMatch, bool outputMatch) = auditLog.verifyRecord(0, "raw input", "raw output");
        assertTrue(inputMatch);
        assertTrue(outputMatch);
    }

    function test_VerifyRecord_DetectsTamperedInput() public {
        auditLog.logAction(
            "run1", "researcher", "SEARCH", keccak256(bytes("raw input")), keccak256(bytes("raw output")), 0, ""
        );

        (bool inputMatch, bool outputMatch) = auditLog.verifyRecord(0, "tampered input", "raw output");
        assertFalse(inputMatch);
        assertTrue(outputMatch);
    }
}
