// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/TrustScoreRegistry.sol";

contract TrustScoreRegistryTest is Test {
    // Redeclared locally (must match TrustScoreRegistry.sol exactly,
    // including indexed-ness) so vm.expectEmit can be used without a
    // qualified emit, which solc 0.8.20 doesn't support for external
    // contract events.
    event ScoreUpdated(
        string indexed agentId, string indexed runId, uint256 newScore, uint256 timestamp, string reason
    );
    event RunStarted(string indexed runId, uint256 timestamp);
    event ScoreClampedWarning(string indexed agentId, string indexed runId, uint256 attemptedScore, uint256 timestamp);

    TrustScoreRegistry registry;

    address owner = address(this);
    address stranger = address(0xBEEF);

    function setUp() public {
        registry = new TrustScoreRegistry();
    }

    // ── constructor / ownership ──────────────────────────────────────────

    function test_OwnerIsDeployer() public view {
        assertEq(registry.owner(), owner);
    }

    function test_RevertWhen_NonOwnerUpdatesScore() public {
        vm.prank(stranger);
        vm.expectRevert("TrustScoreRegistry: caller is not the owner");
        registry.updateScore("researcher", "run1", 80, "reason");
    }

    // ── input validation ─────────────────────────────────────────────────

    function test_RevertWhen_EmptyAgentId() public {
        vm.expectRevert("TrustScoreRegistry: agentId cannot be empty");
        registry.updateScore("", "run1", 80, "reason");
    }

    function test_RevertWhen_EmptyRunId() public {
        vm.expectRevert("TrustScoreRegistry: runId cannot be empty");
        registry.updateScore("researcher", "", 80, "reason");
    }

    // ── updateScore happy path ───────────────────────────────────────────

    function test_UpdateScore_StoresValue() public {
        registry.updateScore("researcher", "run1", 87, "web_search_complete");

        assertEq(registry.getScore("researcher", "run1"), 87);
        assertTrue(registry.hasScore("researcher", "run1"));
        assertEq(registry.lastUpdatedAt("researcher", "run1"), block.timestamp);
    }

    function test_UpdateScore_EmitsScoreUpdated() public {
        vm.expectEmit(true, true, false, true);
        emit ScoreUpdated("researcher", "run1", 87, block.timestamp, "reason");
        registry.updateScore("researcher", "run1", 87, "reason");
    }

    function test_UpdateScore_EmitsRunStartedOnlyOnFirstSeen() public {
        vm.expectEmit(true, false, false, true);
        emit RunStarted("run1", block.timestamp);
        registry.updateScore("researcher", "run1", 50, "reason");

        assertEq(registry.getRunCount(), 1);

        // second update for the same run must NOT emit RunStarted again —
        // recorded indirectly via getRunCount staying at 1
        registry.updateScore("validator", "run1", 60, "reason");
        assertEq(registry.getRunCount(), 1);
    }

    function test_UpdateScore_HistoryAccumulates() public {
        registry.updateScore("researcher", "run1", 50, "step1");
        registry.updateScore("researcher", "run1", 75, "step2");

        TrustScoreRegistry.ScoreUpdate[] memory history = registry.getScoreHistory("researcher", "run1");
        assertEq(history.length, 2);
        assertEq(history[0].score, 50);
        assertEq(history[0].reason, "step1");
        assertEq(history[1].score, 75);
        assertEq(history[1].reason, "step2");

        // current score is the latest update, not the first
        assertEq(registry.getScore("researcher", "run1"), 75);
    }

    // ── safety rail: clamp instead of revert ─────────────────────────────

    function test_UpdateScore_ClampsAboveOneHundred() public {
        registry.updateScore("researcher", "run1", 999, "buggy_score");
        assertEq(registry.getScore("researcher", "run1"), 100);
    }

    function test_UpdateScore_EmitsClampWarningWhenClamped() public {
        vm.expectEmit(true, true, false, true);
        emit ScoreClampedWarning("researcher", "run1", 999, block.timestamp);
        registry.updateScore("researcher", "run1", 999, "buggy_score");
    }

    function test_UpdateScore_DoesNotClampAtExactlyOneHundred() public {
        registry.updateScore("researcher", "run1", 100, "reason");
        assertEq(registry.getScore("researcher", "run1"), 100);
    }

    // ── leaderboard ───────────────────────────────────────────────────────

    function test_GetRunLeaderboard_ReturnsAllAgentsInRun() public {
        registry.updateScore("researcher", "run1", 80, "r");
        registry.updateScore("validator", "run1", 70, "r");
        registry.updateScore("scorer", "run1", 90, "r");

        (string[] memory agentIds, uint256[] memory scores) = registry.getRunLeaderboard("run1");
        assertEq(agentIds.length, 3);
        assertEq(scores[0], 80);
        assertEq(scores[1], 70);
        assertEq(scores[2], 90);
    }

    function test_GetRunLeaderboard_DoesNotDuplicateAgentOnRepeatedUpdate() public {
        registry.updateScore("researcher", "run1", 50, "r");
        registry.updateScore("researcher", "run1", 90, "r");

        (string[] memory agentIds,) = registry.getRunLeaderboard("run1");
        assertEq(agentIds.length, 1);
    }

    // ── resetRun ──────────────────────────────────────────────────────────

    function test_RevertWhen_ResettingUnknownRun() public {
        vm.expectRevert("TrustScoreRegistry: runId does not exist");
        registry.resetRun("no-such-run");
    }

    function test_RevertWhen_NonOwnerResetsRun() public {
        registry.updateScore("researcher", "run1", 80, "r");
        vm.prank(stranger);
        vm.expectRevert("TrustScoreRegistry: caller is not the owner");
        registry.resetRun("run1");
    }

    function test_ResetRun_ClearsScoresAndHistory() public {
        registry.updateScore("researcher", "run1", 80, "r");
        registry.updateScore("validator", "run1", 70, "r");

        registry.resetRun("run1");

        assertEq(registry.getScore("researcher", "run1"), 0);
        assertEq(registry.getScore("validator", "run1"), 0);
        assertFalse(registry.hasScore("researcher", "run1"));
        assertEq(registry.getScoreHistory("researcher", "run1").length, 0);

        // the run itself is still known, just with cleared scores
        assertEq(registry.getRunCount(), 1);
    }

    // ── misc reads ────────────────────────────────────────────────────────

    function test_GetLatestRunId_ReturnsEmptyStringInitially() public view {
        assertEq(registry.getLatestRunId(), "");
    }

    function test_GetLatestRunId_ReturnsMostRecentRun() public {
        registry.updateScore("researcher", "run1", 80, "r");
        registry.updateScore("researcher", "run2", 80, "r");
        assertEq(registry.getLatestRunId(), "run2");
    }

    function test_GetScoreFull_ReflectsState() public {
        registry.updateScore("researcher", "run1", 80, "r");
        registry.updateScore("researcher", "run1", 90, "r");

        TrustScoreRegistry.ScoreRecord memory rec = registry.getScoreFull("researcher", "run1");
        assertEq(rec.currentScore, 90);
        assertEq(rec.updateCount, 2);
        assertTrue(rec.hasScore);
    }
}
