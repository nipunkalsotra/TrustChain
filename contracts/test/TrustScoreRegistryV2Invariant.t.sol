// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/v2/TrustScoreRegistryV2.sol";

/// @notice Stateful-fuzz handler for TrustScoreRegistryV2's invariant suite.
///         Restricts the fuzzer to a small, fixed universe of agentIds/runIds
///         so calls actually collide and exercise real state transitions
///         (hasScore flipping on/off, runAgents growing, resetRun clearing)
///         instead of wandering across an effectively infinite space of
///         never-repeated random strings, which would never re-touch the
///         same (agentId, runId) pair twice and so could never exercise the
///         update-after-reset path where real bugs tend to hide.
contract TrustScoreRegistryV2Handler is Test {
    TrustScoreRegistryV2 public registry;
    address public relayer;
    address public admin;

    string[3] internal AGENTS = ["researcher", "validator", "scorer"];
    string[2] internal RUNS = ["run1", "run2"];

    // Ghost bookkeeping, computed independently of the contract's own
    // storage, so the invariants below check the contract's reported state
    // against an expectation derived a different way — not just internal
    // getter-vs-getter self-consistency.
    uint256 public ghostRunCount;
    mapping(string => bool) internal _ghostRunSeen;

    constructor(TrustScoreRegistryV2 _registry, address _relayer, address _admin) {
        registry = _registry;
        relayer = _relayer;
        admin = _admin;
    }

    function agents() external view returns (string[3] memory) {
        return AGENTS;
    }

    function runs() external view returns (string[2] memory) {
        return RUNS;
    }

    function updateScore(uint256 agentSeed, uint256 runSeed, uint256 score, string calldata reason) public {
        string memory agentId = AGENTS[agentSeed % AGENTS.length];
        string memory runId = RUNS[runSeed % RUNS.length];
        score = bound(score, 0, 200); // spans both the in-range and the clamp-to-100 path

        vm.prank(relayer);
        registry.updateScore(agentId, runId, score, reason);

        if (!_ghostRunSeen[runId]) {
            _ghostRunSeen[runId] = true;
            ghostRunCount++;
        }
    }

    function updateScoresBatch(uint256 runSeed, uint256 scoreA, uint256 scoreB, uint256 scoreC) public {
        string memory runId = RUNS[runSeed % RUNS.length];
        string[] memory ids = new string[](3);
        ids[0] = AGENTS[0];
        ids[1] = AGENTS[1];
        ids[2] = AGENTS[2];
        uint256[] memory vals = new uint256[](3);
        vals[0] = bound(scoreA, 0, 200);
        vals[1] = bound(scoreB, 0, 200);
        vals[2] = bound(scoreC, 0, 200);

        vm.prank(relayer);
        registry.updateScoresBatch(runId, ids, vals, "batch");

        if (!_ghostRunSeen[runId]) {
            _ghostRunSeen[runId] = true;
            ghostRunCount++;
        }
    }

    function resetRun(uint256 runSeed) public {
        string memory runId = RUNS[runSeed % RUNS.length];
        if (!_ghostRunSeen[runId]) return; // resetRun on an unseen runId would revert — not a real call path

        vm.prank(admin);
        registry.resetRun(runId);
    }
}

/// @notice Real Foundry stateful-fuzz invariant suite for
///         TrustScoreRegistryV2 — the Foundry fuzzer drives the handler
///         above through long random sequences of updateScore /
///         updateScoresBatch / resetRun calls, and after EVERY call in
///         every sequence, all invariant_* functions below must still
///         hold. This is a genuinely different technique from the
///         example-based unit tests in TrustScoreRegistryV2.t.sol: those
///         check specific hand-picked call sequences, this checks that no
///         REACHABLE sequence (within the bounded agent/run universe)
///         breaks a safety property.
contract TrustScoreRegistryV2InvariantTest is Test {
    TrustScoreRegistryV2 registry;
    TrustScoreRegistryV2Handler handler;

    address admin = address(this);
    address relayer = address(0xBEEF);

    function setUp() public {
        registry = new TrustScoreRegistryV2(admin);
        registry.grantRole(registry.ANCHOR_ROLE(), relayer);

        handler = new TrustScoreRegistryV2Handler(registry, relayer, admin);
        targetContract(address(handler));
    }

    /// @dev score is clamped to <= 100 in _updateScore before it's ever
    ///      stored — this must hold no matter what sequence of calls (with
    ///      seed scores up to 200) got us here.
    function invariant_ScoreNeverExceeds100() public view {
        string[3] memory agentIds = handler.agents();
        string[2] memory runIds = handler.runs();
        for (uint256 a = 0; a < agentIds.length; a++) {
            for (uint256 r = 0; r < runIds.length; r++) {
                assertLe(registry.getScore(agentIds[a], runIds[r]), 100);
            }
        }
    }

    /// @dev The STORAGE PACKING change (see TrustScoreRegistryV2.sol) split
    ///      one logical value across getScore/scores/getScoreFull — a real
    ///      regression risk is these three getters drifting out of sync
    ///      after some call sequence. They must always agree.
    function invariant_ScoreGettersAgreeWithEachOther() public view {
        string[3] memory agentIds = handler.agents();
        string[2] memory runIds = handler.runs();
        for (uint256 a = 0; a < agentIds.length; a++) {
            for (uint256 r = 0; r < runIds.length; r++) {
                uint256 viaGetScore = registry.getScore(agentIds[a], runIds[r]);
                uint256 viaScores = registry.scores(agentIds[a], runIds[r]);
                TrustScoreRegistryV2.ScoreRecord memory full = registry.getScoreFull(agentIds[a], runIds[r]);
                assertEq(viaGetScore, viaScores);
                assertEq(viaGetScore, full.currentScore);
            }
        }
    }

    /// @dev hasScore(...) and getScoreHistory(...).length must always agree
    ///      on whether an (agentId, runId) pair has ever been scored (and
    ///      not since reset) — they're set/cleared together everywhere in
    ///      the contract.
    function invariant_HasScoreMatchesHistoryPresence() public view {
        string[3] memory agentIds = handler.agents();
        string[2] memory runIds = handler.runs();
        for (uint256 a = 0; a < agentIds.length; a++) {
            for (uint256 r = 0; r < runIds.length; r++) {
                bool has = registry.hasScore(agentIds[a], runIds[r]);
                uint256 historyLen = registry.getScoreHistory(agentIds[a], runIds[r]).length;
                assertEq(has, historyLen > 0);
            }
        }
    }

    /// @dev getRunCount() must always equal the number of distinct runIds
    ///      ever passed to updateScore/updateScoresBatch, independent of
    ///      how many times resetRun was called on any of them.
    function invariant_RunCountMatchesUniqueRunsSeen() public view {
        assertEq(registry.getRunCount(), handler.ghostRunCount());
    }

    /// @dev A run's leaderboard must never list the same agentId twice —
    ///      even across a resetRun() followed by re-scoring the same
    ///      agent, which is exactly the sequence a hand-picked unit test
    ///      is unlikely to think to write but the fuzzer reliably finds.
    function invariant_RunLeaderboardHasNoDuplicateAgents() public view {
        string[2] memory runIds = handler.runs();
        for (uint256 r = 0; r < runIds.length; r++) {
            (string[] memory ids,) = registry.getRunLeaderboard(runIds[r]);
            for (uint256 i = 0; i < ids.length; i++) {
                for (uint256 j = i + 1; j < ids.length; j++) {
                    assertFalse(keccak256(bytes(ids[i])) == keccak256(bytes(ids[j])));
                }
            }
        }
    }
}
