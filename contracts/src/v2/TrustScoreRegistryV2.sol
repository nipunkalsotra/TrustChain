// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";

/**
 * @title  TrustScoreRegistryV2
 * @notice Per-agent, per-run trust scores (0-100), with history for
 *         sparkline charts and a per-run leaderboard.
 *
 * Functionally identical to V1's read surface (unchanged so the indexer and
 * frontend queries built against it keep working); the change is the trust
 * model — V1 had a single owner with unlimited authority, V2 splits that
 * into ANCHOR_ROLE (may only write scores, held by the anchor worker) and
 * DEFAULT_ADMIN_ROLE (may pause/manage roles, intended for a multisig).
 * agentId/runId stay as human-readable strings rather than bytes32 hashes
 * — unlike the audit log's free-text action/metadata fields, these are a
 * small, fixed vocabulary (4 agent ids) looked up directly by every read
 * path, and hashing them would only add an indirection cost with no
 * storage-cost benefit worth the readability loss.
 *
 * STORAGE PACKING: the original layout stored score/lastUpdatedAt/
 * hasScore as three SEPARATE `uint256`/`uint256`/`bool` mappings — three
 * full 32-byte storage slots per (agentId, runId) pair, and three
 * separate SSTOREs on every _updateScore() call, even though `score` is
 * always 0-100 (fits in a uint8) and `timestamp` is a unix second count
 * (fits comfortably in a uint40 — valid until roughly the year 36,812).
 * Packed into one `ScoreState` struct (uint8 + uint40 + bool = 6 of 32
 * bytes, one slot), every _updateScore() call now does exactly ONE SSTORE
 * for this data instead of three. Same packing applied to `ScoreUpdate`
 * (the per-run history array `scoreHistory` grows by one entry on EVERY
 * score update ever recorded, making it the single highest-frequency
 * write path in this contract) — score+timestamp share one slot there
 * too, `reason` keeps its own dynamic slot as before (a string can't
 * pack into a fixed-size struct slot regardless).
 *
 * scores/lastUpdatedAt/hasScore stay PUBLIC, EXTERNAL FUNCTIONS below
 * with the exact same name/signature/return-type Solidity's own
 * auto-generated mapping getters produced before this change — every
 * existing caller (contract.functions.scores(...), .hasScore(...),
 * .lastUpdatedAt(...) — see backend/tests/test_score_writer.py and
 * contracts/test/TrustScoreRegistryV2.t.sol) keeps working completely
 * unchanged; only the underlying storage layout is different, not the
 * ABI.
 */
contract TrustScoreRegistryV2 is AccessControl, Pausable {
    bytes32 public constant ANCHOR_ROLE = keccak256("ANCHOR_ROLE");

    struct ScoreUpdate {
        uint8 score;
        uint40 timestamp;
        string reason;
    }

    struct ScoreRecord {
        uint256 currentScore;
        uint256 updateCount;
        uint256 lastUpdatedAt;
        bool hasScore;
    }

    struct ScoreState {
        uint8 score;
        uint40 lastUpdatedAt;
        bool hasScore;
    }

    mapping(string => mapping(string => ScoreState)) internal _scoreState;
    mapping(string => mapping(string => ScoreUpdate[])) public scoreHistory;
    mapping(string => string[]) public runAgents;
    mapping(string => bool) public runExists;
    string[] public allRunIds;

    // agentId/runId are deliberately NOT `indexed`: Solidity only stores the
    // keccak256 HASH of an indexed dynamic type (string/bytes) in a log's
    // topics, not the value itself — an indexed string here would make the
    // human-readable id permanently unrecoverable from the event, which
    // breaks the read model's core invariant (see db/models.py's
    // ReadModelScore docstring: rm_scores must be a pure, replayable
    // function of these events). Filtering by agentId/runId isn't a
    // real on-chain use case for a 4-agent, per-run vocabulary this
    // small anyway — the indexer decodes every event and filters
    // in Postgres instead.
    event ScoreUpdated(string agentId, string runId, uint256 newScore, uint256 timestamp, string reason);
    event RunStarted(string runId, uint256 timestamp);
    event ScoreClampedWarning(string agentId, string runId, uint256 attemptedScore, uint256 timestamp);

    modifier validIds(string calldata agentId, string calldata runId) {
        require(bytes(agentId).length > 0, "TrustScoreRegistryV2: agentId cannot be empty");
        require(bytes(runId).length > 0, "TrustScoreRegistryV2: runId cannot be empty");
        _;
    }

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    function updateScore(string calldata agentId, string calldata runId, uint256 score, string calldata reason)
        external
        onlyRole(ANCHOR_ROLE)
        whenNotPaused
        validIds(agentId, runId)
    {
        _updateScore(agentId, runId, score, reason);
    }

    /// @notice Batch variant — one call for all four agents' scores at the
    ///         end of a run, instead of four separate transactions.
    function updateScoresBatch(
        string calldata runId,
        string[] calldata agentIds,
        uint256[] calldata scoreValues,
        string calldata reason
    ) external onlyRole(ANCHOR_ROLE) whenNotPaused {
        require(agentIds.length == scoreValues.length, "TrustScoreRegistryV2: length mismatch");
        require(bytes(runId).length > 0, "TrustScoreRegistryV2: runId cannot be empty");
        for (uint256 i = 0; i < agentIds.length; i++) {
            require(bytes(agentIds[i]).length > 0, "TrustScoreRegistryV2: agentId cannot be empty");
            _updateScore(agentIds[i], runId, scoreValues[i], reason);
        }
    }

    /// @dev Shared write path for updateScore/updateScoresBatch. Internal,
    ///      not external — a `this.updateScore(...)` call from within the
    ///      batch loop would make the CONTRACT ITSELF msg.sender for that
    ///      inner call, breaking the ANCHOR_ROLE check for the real caller.
    function _updateScore(string calldata agentId, string calldata runId, uint256 score, string calldata reason)
        internal
    {
        uint256 safeScore = score;
        if (score > 100) {
            emit ScoreClampedWarning(agentId, runId, score, block.timestamp);
            safeScore = 100;
        }

        if (!runExists[runId]) {
            runExists[runId] = true;
            allRunIds.push(runId);
            emit RunStarted(runId, block.timestamp);
        }

        if (!_scoreState[agentId][runId].hasScore) {
            runAgents[runId].push(agentId);
        }

        // casting to 'uint8' is safe because safeScore is clamped to <= 100 above
        // forge-lint: disable-next-line(unsafe-typecast)
        ScoreState memory newState =
            ScoreState({score: uint8(safeScore), lastUpdatedAt: uint40(block.timestamp), hasScore: true});
        _scoreState[agentId][runId] = newState;
        // forge-lint: disable-next-line(unsafe-typecast)
        ScoreUpdate memory newUpdate =
            ScoreUpdate({score: uint8(safeScore), timestamp: uint40(block.timestamp), reason: reason});
        scoreHistory[agentId][runId].push(newUpdate);

        emit ScoreUpdated(agentId, runId, safeScore, block.timestamp, reason);
    }

    function resetRun(string calldata runId) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(runExists[runId], "TrustScoreRegistryV2: runId does not exist");

        string[] storage agents = runAgents[runId];
        for (uint256 i = 0; i < agents.length; i++) {
            string memory agentId = agents[i];
            delete _scoreState[agentId][runId];
            delete scoreHistory[agentId][runId];
        }
        // Without this, re-scoring an agent after a reset would push it
        // into runAgents[runId] a SECOND time — _updateScore only guards
        // against duplicate pushes via hasScore, which resetRun already
        // clears above, but never clears the list itself. Found by the
        // updateScore -> resetRun -> updateScore(batch) sequence Foundry's
        // stateful fuzzer discovered in
        // test/TrustScoreRegistryV2Invariant.t.sol's
        // invariant_RunLeaderboardHasNoDuplicateAgents.
        delete runAgents[runId];
    }

    /// @notice Current score for (agentId, runId). Hand-written to match the
    ///         exact name/signature Solidity's auto-generated getter for the
    ///         original standalone `scores` mapping produced — see the
    ///         STORAGE PACKING note above.
    function scores(string calldata agentId, string calldata runId) external view returns (uint256) {
        return _scoreState[agentId][runId].score;
    }

    /// @notice Unix timestamp of the last score update, or 0 if none yet.
    ///         Hand-written to match the original `lastUpdatedAt` mapping
    ///         getter's name/signature — see the STORAGE PACKING note above.
    function lastUpdatedAt(string calldata agentId, string calldata runId) external view returns (uint256) {
        return _scoreState[agentId][runId].lastUpdatedAt;
    }

    /// @notice Whether (agentId, runId) has ever received a score. Hand-
    ///         written to match the original `hasScore` mapping getter's
    ///         name/signature — see the STORAGE PACKING note above.
    function hasScore(string calldata agentId, string calldata runId) external view returns (bool) {
        return _scoreState[agentId][runId].hasScore;
    }

    function getScore(string calldata agentId, string calldata runId) external view returns (uint256) {
        return _scoreState[agentId][runId].score;
    }

    function getScoreFull(string calldata agentId, string calldata runId) external view returns (ScoreRecord memory) {
        ScoreState memory state = _scoreState[agentId][runId];
        return ScoreRecord({
            currentScore: state.score,
            updateCount: scoreHistory[agentId][runId].length,
            lastUpdatedAt: state.lastUpdatedAt,
            hasScore: state.hasScore
        });
    }

    function getScoreHistory(string calldata agentId, string calldata runId)
        external
        view
        returns (ScoreUpdate[] memory)
    {
        return scoreHistory[agentId][runId];
    }

    function getRunLeaderboard(string calldata runId)
        external
        view
        returns (string[] memory agentIds, uint256[] memory agentScores)
    {
        string[] storage agents = runAgents[runId];
        uint256 count = agents.length;

        agentIds = new string[](count);
        agentScores = new uint256[](count);

        for (uint256 i = 0; i < count; i++) {
            agentIds[i] = agents[i];
            agentScores[i] = _scoreState[agents[i]][runId].score;
        }
    }

    function getRunCount() external view returns (uint256) {
        return allRunIds.length;
    }

    function getLatestRunId() external view returns (string memory) {
        if (allRunIds.length == 0) return "";
        return allRunIds[allRunIds.length - 1];
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }
}
