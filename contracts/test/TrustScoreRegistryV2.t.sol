// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/TrustScoreRegistryV2.sol";

contract TrustScoreRegistryV2Test is Test {
    TrustScoreRegistryV2 registry;

    address admin = address(this);
    address relayer = address(0xBEEF);
    address stranger = address(0xDEAD);

    function setUp() public {
        registry = new TrustScoreRegistryV2(admin);
        registry.grantRole(registry.ANCHOR_ROLE(), relayer);
    }

    // ── Role separation ──────────────────────────────────────────────────

    function test_RevertWhen_StrangerUpdatesScore() public {
        vm.prank(stranger);
        vm.expectRevert();
        registry.updateScore("researcher", "run1", 80, "r");
    }

    function test_RevertWhen_AdminWithoutAnchorRoleUpdatesScore() public {
        vm.expectRevert();
        registry.updateScore("researcher", "run1", 80, "r");
    }

    function test_RevertWhen_RelayerResetsRun() public {
        vm.prank(relayer);
        registry.updateScore("researcher", "run1", 80, "r");

        vm.prank(relayer);
        vm.expectRevert();
        registry.resetRun("run1");
    }

    function test_RevertWhen_RelayerPauses() public {
        vm.prank(relayer);
        vm.expectRevert();
        registry.pause();
    }

    function test_UpdateScoreResumesAfterUnpause() public {
        registry.pause();

        vm.prank(relayer);
        vm.expectRevert();
        registry.updateScore("researcher", "run1", 80, "r");

        registry.unpause();

        vm.prank(relayer);
        registry.updateScore("researcher", "run1", 80, "r");
        assertEq(registry.getScore("researcher", "run1"), 80);
    }

    // ── updateScore ───────────────────────────────────────────────────────

    function test_UpdateScore_StoresAndClamps() public {
        vm.startPrank(relayer);
        registry.updateScore("researcher", "run1", 999, "buggy");
        vm.stopPrank();

        assertEq(registry.getScore("researcher", "run1"), 100);
        // scores/lastUpdatedAt/hasScore are hand-written external functions
        // (see the STORAGE PACKING note in TrustScoreRegistryV2.sol) standing
        // in for what used to be auto-generated public-mapping getters —
        // exercise them directly, not just via getScore/getScoreFull.
        assertEq(registry.scores("researcher", "run1"), 100);
        assertEq(registry.lastUpdatedAt("researcher", "run1"), block.timestamp);
        assertTrue(registry.hasScore("researcher", "run1"));
    }

    function test_GetScoreFull_ReturnsCurrentStateAndUpdateCount() public {
        vm.startPrank(relayer);
        registry.updateScore("researcher", "run1", 50, "step1");
        registry.updateScore("researcher", "run1", 90, "step2");
        vm.stopPrank();

        TrustScoreRegistryV2.ScoreRecord memory record = registry.getScoreFull("researcher", "run1");
        assertEq(record.currentScore, 90);
        assertEq(record.updateCount, 2);
        assertEq(record.lastUpdatedAt, block.timestamp);
        assertTrue(record.hasScore);
    }

    function test_GetScoreFull_UnscoredAgentReturnsZeroedRecord() public view {
        TrustScoreRegistryV2.ScoreRecord memory record = registry.getScoreFull("researcher", "never_run");
        assertEq(record.currentScore, 0);
        assertEq(record.updateCount, 0);
        assertEq(record.lastUpdatedAt, 0);
        assertFalse(record.hasScore);
    }

    function test_UpdateScore_HistoryAccumulates() public {
        vm.startPrank(relayer);
        registry.updateScore("researcher", "run1", 50, "step1");
        registry.updateScore("researcher", "run1", 90, "step2");
        vm.stopPrank();

        TrustScoreRegistryV2.ScoreUpdate[] memory history = registry.getScoreHistory("researcher", "run1");
        assertEq(history.length, 2);
        assertEq(history[1].score, 90);
    }

    // ── updateScoresBatch — the new V2 capability ───────────────────────────

    function test_UpdateScoresBatch_WritesAllAgents() public {
        string[] memory agents = new string[](4);
        agents[0] = "researcher";
        agents[1] = "validator";
        agents[2] = "scorer";
        agents[3] = "reporter";

        uint256[] memory scores = new uint256[](4);
        scores[0] = 80;
        scores[1] = 70;
        scores[2] = 90;
        scores[3] = 60;

        vm.prank(relayer);
        registry.updateScoresBatch("run1", agents, scores, "pipeline_scoring");

        assertEq(registry.getScore("researcher", "run1"), 80);
        assertEq(registry.getScore("validator", "run1"), 70);
        assertEq(registry.getScore("scorer", "run1"), 90);
        assertEq(registry.getScore("reporter", "run1"), 60);

        (string[] memory ids, uint256[] memory vals) = registry.getRunLeaderboard("run1");
        assertEq(ids.length, 4);
        assertEq(vals[2], 90);
    }

    function test_UpdateScoresBatch_RespectsRoleCheck_NotBypassedByInternalCall() public {
        // Regression guard for the specific bug this contract's
        // implementation had to avoid: routing the batch loop through an
        // external `this.updateScore(...)` call would make msg.sender the
        // CONTRACT ITSELF for the inner call, and — since the contract was
        // never granted ANCHOR_ROLE — that would revert even for a
        // legitimate relayer caller. If this test fails, that regression
        // has come back.
        string[] memory agents = new string[](1);
        agents[0] = "researcher";
        uint256[] memory scores = new uint256[](1);
        scores[0] = 80;

        vm.prank(relayer);
        registry.updateScoresBatch("run1", agents, scores, "r");

        assertEq(registry.getScore("researcher", "run1"), 80);
    }

    function test_RevertWhen_BatchLengthMismatch() public {
        string[] memory agents = new string[](2);
        agents[0] = "researcher";
        agents[1] = "validator";
        uint256[] memory scores = new uint256[](1);
        scores[0] = 80;

        vm.prank(relayer);
        vm.expectRevert("TrustScoreRegistryV2: length mismatch");
        registry.updateScoresBatch("run1", agents, scores, "r");
    }

    function test_RevertWhen_BatchRunIdEmpty() public {
        string[] memory agents = new string[](1);
        agents[0] = "researcher";
        uint256[] memory scores = new uint256[](1);
        scores[0] = 80;

        vm.prank(relayer);
        vm.expectRevert("TrustScoreRegistryV2: runId cannot be empty");
        registry.updateScoresBatch("", agents, scores, "r");
    }

    function test_RevertWhen_BatchAgentIdEmpty() public {
        string[] memory agents = new string[](2);
        agents[0] = "researcher";
        agents[1] = "";
        uint256[] memory scores = new uint256[](2);
        scores[0] = 80;
        scores[1] = 70;

        vm.prank(relayer);
        vm.expectRevert("TrustScoreRegistryV2: agentId cannot be empty");
        registry.updateScoresBatch("run1", agents, scores, "r");
    }

    // ── updateScore's validIds modifier — the single-score path has its
    //    own empty-agentId/empty-runId requires, distinct from
    //    updateScoresBatch's (tested above) ─────────────────────────────

    function test_RevertWhen_UpdateScoreAgentIdEmpty() public {
        vm.prank(relayer);
        vm.expectRevert("TrustScoreRegistryV2: agentId cannot be empty");
        registry.updateScore("", "run1", 80, "r");
    }

    function test_RevertWhen_UpdateScoreRunIdEmpty() public {
        vm.prank(relayer);
        vm.expectRevert("TrustScoreRegistryV2: runId cannot be empty");
        registry.updateScore("researcher", "", 80, "r");
    }

    // ── resetRun (admin-only) ────────────────────────────────────────────

    function test_ResetRun_ClearsScores() public {
        vm.prank(relayer);
        registry.updateScore("researcher", "run1", 80, "r");

        registry.resetRun("run1"); // admin (this test contract) calls directly

        assertEq(registry.getScore("researcher", "run1"), 0);
        assertFalse(registry.hasScore("researcher", "run1"));
    }

    function test_ResetRun_ThenRescoringSameAgentDoesNotDuplicateLeaderboardEntry() public {
        // Regression test for a bug Foundry's stateful fuzzer found in
        // test/TrustScoreRegistryV2Invariant.t.sol
        // (invariant_RunLeaderboardHasNoDuplicateAgents): resetRun cleared
        // each agent's score/history but never cleared runAgents[runId]
        // itself, so re-scoring the same agent after a reset pushed it
        // into the leaderboard array a second time.
        vm.prank(relayer);
        registry.updateScore("researcher", "run1", 80, "r");

        registry.resetRun("run1");

        vm.prank(relayer);
        registry.updateScore("researcher", "run1", 90, "r");

        (string[] memory ids,) = registry.getRunLeaderboard("run1");
        assertEq(ids.length, 1);
        assertEq(ids[0], "researcher");
    }

    function test_RevertWhen_ResettingUnknownRunId() public {
        vm.expectRevert("TrustScoreRegistryV2: runId does not exist");
        registry.resetRun("never_seen_run");
    }

    // ── getLatestRunId ────────────────────────────────────────────────────

    function test_GetLatestRunId_EmptyStringWhenNoRunsYet() public view {
        assertEq(registry.getLatestRunId(), "");
    }

    function test_GetLatestRunId_ReturnsMostRecentlySeenRun() public {
        vm.startPrank(relayer);
        registry.updateScore("researcher", "run1", 80, "r");
        registry.updateScore("researcher", "run2", 90, "r");
        vm.stopPrank();

        assertEq(registry.getLatestRunId(), "run2");
        assertEq(registry.getRunCount(), 2);
    }
}
