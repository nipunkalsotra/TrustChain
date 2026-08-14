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
 */
contract TrustScoreRegistryV2 is AccessControl, Pausable {
    bytes32 public constant ANCHOR_ROLE = keccak256("ANCHOR_ROLE");

    struct ScoreUpdate {
        uint256 score;
        uint256 timestamp;
        string reason;
    }

    struct ScoreRecord {
        uint256 currentScore;
        uint256 updateCount;
        uint256 lastUpdatedAt;
        bool hasScore;
    }

    mapping(string => mapping(string => uint256)) public scores;
    mapping(string => mapping(string => ScoreUpdate[])) public scoreHistory;
    mapping(string => mapping(string => uint256)) public lastUpdatedAt;
    mapping(string => mapping(string => bool)) public hasScore;
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

        if (!hasScore[agentId][runId]) {
            runAgents[runId].push(agentId);
        }

        scores[agentId][runId] = safeScore;
        lastUpdatedAt[agentId][runId] = block.timestamp;
        hasScore[agentId][runId] = true;
        scoreHistory[agentId][runId].push(ScoreUpdate({score: safeScore, timestamp: block.timestamp, reason: reason}));

        emit ScoreUpdated(agentId, runId, safeScore, block.timestamp, reason);
    }

    function resetRun(string calldata runId) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(runExists[runId], "TrustScoreRegistryV2: runId does not exist");

        string[] storage agents = runAgents[runId];
        for (uint256 i = 0; i < agents.length; i++) {
            string memory agentId = agents[i];
            scores[agentId][runId] = 0;
            lastUpdatedAt[agentId][runId] = 0;
            hasScore[agentId][runId] = false;
            delete scoreHistory[agentId][runId];
        }
    }

    function getScore(string calldata agentId, string calldata runId) external view returns (uint256) {
        return scores[agentId][runId];
    }

    function getScoreFull(string calldata agentId, string calldata runId) external view returns (ScoreRecord memory) {
        return ScoreRecord({
            currentScore: scores[agentId][runId],
            updateCount: scoreHistory[agentId][runId].length,
            lastUpdatedAt: lastUpdatedAt[agentId][runId],
            hasScore: hasScore[agentId][runId]
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
            agentScores[i] = scores[agents[i]][runId];
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
