// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  TrustScoreRegistry
 * @notice Contract 2 of TrustChain — deploy this SECOND.
 *
 * PURPOSE
 * -------
 * Stores a trust score (0–100) per agent per run on-chain.
 * Your Next.js gauge panels read from this contract in real time.
 *
 * DECISIONS LOCKED IN (from design doc)
 * ──────────────────────────────────────
 * 1. ACCESS CONTROL    → onlyOwner.  Only your wallet can call updateScore().
 *                        Prevents anyone on Monad testnet from faking scores.
 *
 * 2. SCORE CALCULATION → Python (Scorer agent) computes the value and passes it in.
 *                        Contract just stores it.  Simple, hackathon-safe.
 *
 * 3. RESET BEHAVIOUR   → Resets per run (runId-based).
 *                        mapping: agentId → runId → score
 *                        Every new demo run starts at 0.  Judges see a fresh score.
 *                        Cumulative history still queryable for any past runId.
 *
 * 4. SAFETY RAIL       → require(score <= 100) inside updateScore().
 *                        A Python bug pushing 999 won't break the frontend gauges.
 *
 * DEPLOY ORDER
 * ────────────
 * 1. Deploy TrustScoreRegistry  →  save TRUST_SCORE_ADDRESS
 * 2. Deploy AgentAuditLog(TRUST_SCORE_ADDRESS)
 * You do NOT need to call setAuditLog() here because access is onlyOwner,
 * not onlyAuditLog — your FastAPI wallet calls updateScore() directly.
 */

contract TrustScoreRegistry {

    // ─────────────────────────────────────────────
    // STRUCTS
    // ─────────────────────────────────────────────

    /**
     * @dev A single score update record — stored in history arrays.
     *      Lets the frontend render a sparkline of score changes over time.
     */
    struct ScoreUpdate {
        uint256 score;       // the new score value (0–100)
        uint256 timestamp;   // block.timestamp when updated
        string  reason;      // e.g. "web_search_complete", "validation_passed"
    }

    /**
     * @dev Return type for getScoreFull() — everything the UI needs in one call.
     */
    struct ScoreRecord {
        uint256 currentScore;    // latest score for this agent in this run
        uint256 updateCount;     // how many times score was updated this run
        uint256 lastUpdatedAt;   // timestamp of most recent update
        bool    hasScore;        // false if no score recorded yet for this run
    }


    // ─────────────────────────────────────────────
    // STATE VARIABLES
    // ─────────────────────────────────────────────

    /// @notice Contract deployer — only this address can update scores.
    address public owner;

    /// @notice agentId → runId → current score (0–100)
    ///         Primary mapping — cheap read for the gauge panels.
    ///         e.g. scores["researcher"]["run_001"] = 87
    mapping(string => mapping(string => uint256)) public scores;

    /// @notice agentId → runId → ScoreUpdate[]
    ///         Full history of every update within a run.
    ///         Used by the sparkline chart in the audit panel.
    mapping(string => mapping(string => ScoreUpdate[])) public scoreHistory;

    /// @notice agentId → runId → last updated timestamp
    ///         Separate field so the UI can show "last updated X sec ago"
    ///         without loading the full history array.
    mapping(string => mapping(string => uint256)) public lastUpdatedAt;

    /// @notice agentId → runId → bool (has at least one score been set?)
    ///         Needed to distinguish "score=0 because not started" from
    ///         "score=0 because agent failed everything".
    mapping(string => mapping(string => bool)) public hasScore;

    /// @notice Tracks all runIds ever used — for the leaderboard view.
    ///         runId → agentId[] (which agents ran in this run)
    mapping(string => string[]) public runAgents;

    /// @notice runId → bool  — so we can check if a runId exists.
    mapping(string => bool) public runExists;

    /// @notice Ordered list of all runIds — for UI pagination.
    string[] public allRunIds;


    // ─────────────────────────────────────────────
    // EVENTS
    // ─────────────────────────────────────────────

    /**
     * @dev Emitted every time a score is updated.
     *      Web3.py event listener catches this → pushes to SSE → gauge animates.
     *      agentId + runId are indexed for fast filtering.
     */
    event ScoreUpdated(
        string  indexed agentId,
        string  indexed runId,
        uint256         newScore,
        uint256         timestamp,
        string          reason
    );

    /**
     * @dev Emitted when a new runId is seen for the first time.
     *      Lets the frontend know a fresh demo run has started.
     */
    event RunStarted(
        string  indexed runId,
        uint256         timestamp
    );

    /**
     * @dev Emitted when the safety rail is hit (score > 100 was attempted).
     *      Helps you debug a Python-side bug during the demo.
     */
    event ScoreClampedWarning(
        string  indexed agentId,
        string  indexed runId,
        uint256         attemptedScore,
        uint256         timestamp
    );


    // ─────────────────────────────────────────────
    // MODIFIERS
    // ─────────────────────────────────────────────

    /**
     * @dev Only the owner wallet (your FastAPI private key address) can
     *      update scores.  Anyone else gets an immediate revert.
     */
    modifier onlyOwner() {
        require(
            msg.sender == owner,
            "TrustScoreRegistry: caller is not the owner"
        );
        _;
    }

    /**
     * @dev Validates both agentId and runId are non-empty strings.
     *      Catches typos in Python before they land on-chain.
     */
    modifier validIds(string calldata agentId, string calldata runId) {
        require(bytes(agentId).length > 0, "TrustScoreRegistry: agentId cannot be empty");
        require(bytes(runId).length  > 0,  "TrustScoreRegistry: runId cannot be empty");
        _;
    }


    // ─────────────────────────────────────────────
    // CONSTRUCTOR
    // ─────────────────────────────────────────────

    /**
     * @dev Sets the deployer wallet as owner.
     *      Run once: npx hardhat run scripts/deploy.js --network monad_testnet
     */
    constructor() {
        owner = msg.sender;
    }


    // ─────────────────────────────────────────────
    // WRITE FUNCTIONS  (cost gas — called by FastAPI via Web3.py)
    // ─────────────────────────────────────────────

    /**
     * @notice Update the trust score for an agent in a specific run.
     * @dev    Called by FastAPI after the Scorer agent returns a score.
     *         Python computes the score value; this contract just stores it.
     *
     *         SAFETY RAIL: if score > 100, we clamp to 100, emit a warning,
     *         and continue — we do NOT revert, so the demo never halts from
     *         a Python bug pushing an out-of-range value.
     *
     * @param agentId   e.g. "researcher", "validator", "scorer", "reporter"
     * @param runId     e.g. "run_20240115_001" — unique per demo run, set by FastAPI
     * @param score     0–100.  Python's Scorer agent computes and passes this in.
     * @param reason    Short label, e.g. "web_search_complete" — shown in audit panel
     *
     * Python side (Web3.py):
     *   contract.functions.updateScore("researcher", run_id, 87, "web_search_complete")
     *           .build_transaction({from: account.address, gas: 150000, nonce: nonce})
     */
    function updateScore(
        string  calldata agentId,
        string  calldata runId,
        uint256          score,
        string  calldata reason
    ) external onlyOwner validIds(agentId, runId) {

        // ── Safety rail: clamp score to 100, never revert ──────────────────
        uint256 safeScore = score;
        if (score > 100) {
            emit ScoreClampedWarning(agentId, runId, score, block.timestamp);
            safeScore = 100;
        }

        // ── Register new runId if this is the first time we see it ─────────
        if (!runExists[runId]) {
            runExists[runId] = true;
            allRunIds.push(runId);
            emit RunStarted(runId, block.timestamp);
        }

        // ── Track which agents participated in this run ────────────────────
        if (!hasScore[agentId][runId]) {
            runAgents[runId].push(agentId);
        }

        // ── Store the score ────────────────────────────────────────────────
        scores[agentId][runId]        = safeScore;
        lastUpdatedAt[agentId][runId] = block.timestamp;
        hasScore[agentId][runId]      = true;

        // ── Append to history (for sparkline chart) ────────────────────────
        scoreHistory[agentId][runId].push(ScoreUpdate({
            score:     safeScore,
            timestamp: block.timestamp,
            reason:    reason
        }));

        emit ScoreUpdated(agentId, runId, safeScore, block.timestamp, reason);
    }

    /**
     * @notice Reset all scores for a given runId to zero.
     * @dev    Optional utility — call this if you want to cleanly restart a demo
     *         run without deploying a new contract.  Clears both current scores
     *         and history for every agent in this run.
     *
     *         NOTE: This does NOT delete the run from allRunIds — the run is still
     *         visible in history, just with cleared scores.
     *
     * @param runId   The run to reset.
     */
    function resetRun(
        string calldata runId
    ) external onlyOwner {

        require(runExists[runId], "TrustScoreRegistry: runId does not exist");

        string[] storage agents = runAgents[runId];
        for (uint256 i = 0; i < agents.length; i++) {
            string memory agentId = agents[i];
            scores[agentId][runId]        = 0;
            lastUpdatedAt[agentId][runId] = 0;
            hasScore[agentId][runId]      = false;
            delete scoreHistory[agentId][runId];
        }
    }


    // ─────────────────────────────────────────────
    // READ / VIEW FUNCTIONS  (free — no gas)
    // ─────────────────────────────────────────────

    /**
     * @notice Get the current score for an agent in a run.
     * @dev    Cheapest read — used by the gauge panels in Next.js.
     *         Polls this every few seconds or triggers on ScoreUpdated event.
     *
     * @param agentId  e.g. "researcher"
     * @param runId    e.g. "run_20240115_001"
     * @return uint256  Current score (0–100).  Returns 0 if not yet scored.
     */
    function getScore(
        string calldata agentId,
        string calldata runId
    ) external view returns (uint256) {
        return scores[agentId][runId];
    }

    /**
     * @notice Get full score record — current score + metadata.
     * @dev    Used by the Trust Score panel to show score + last updated time
     *         + whether scoring has started yet for this agent.
     *
     * @param agentId  e.g. "researcher"
     * @param runId    e.g. "run_20240115_001"
     * @return ScoreRecord struct
     */
    function getScoreFull(
        string calldata agentId,
        string calldata runId
    ) external view returns (ScoreRecord memory) {
        return ScoreRecord({
            currentScore:  scores[agentId][runId],
            updateCount:   scoreHistory[agentId][runId].length,
            lastUpdatedAt: lastUpdatedAt[agentId][runId],
            hasScore:      hasScore[agentId][runId]
        });
    }

    /**
     * @notice Get the full score history for an agent in a run.
     * @dev    Used by the sparkline chart in the audit panel.
     *         Returns every ScoreUpdate ever pushed for this agent+run.
     *
     * @param agentId  e.g. "researcher"
     * @param runId    e.g. "run_20240115_001"
     * @return ScoreUpdate[]  Array of {score, timestamp, reason}
     */
    function getScoreHistory(
        string calldata agentId,
        string calldata runId
    ) external view returns (ScoreUpdate[] memory) {
        return scoreHistory[agentId][runId];
    }

    /**
     * @notice Get scores for ALL agents in a run — for the leaderboard view.
     * @dev    Returns parallel arrays: agentIds[] and their scores[].
     *         Frontend zips them together for the leaderboard table.
     *
     * @param runId  The run to query.
     * @return agentIds  Array of agentId strings
     * @return agentScores  Corresponding scores (same index)
     */
    function getRunLeaderboard(
        string calldata runId
    ) external view returns (
        string[] memory agentIds,
        uint256[] memory agentScores
    ) {
        string[] storage agents = runAgents[runId];
        uint256 count = agents.length;

        agentIds    = new string[](count);
        agentScores = new uint256[](count);

        for (uint256 i = 0; i < count; i++) {
            agentIds[i]    = agents[i];
            agentScores[i] = scores[agents[i]][runId];
        }

        return (agentIds, agentScores);
    }

    /**
     * @notice Total number of runs ever recorded.
     * @dev    Used by the UI to show "X demo runs completed" and paginate history.
     */
    function getRunCount() external view returns (uint256) {
        return allRunIds.length;
    }

    /**
     * @notice Returns the most recent runId — the active demo run.
     * @dev    FastAPI can call this to confirm the current runId is registered.
     *         Returns an empty string if no runs exist yet.
     */
    function getLatestRunId() external view returns (string memory) {
        if (allRunIds.length == 0) return "";
        return allRunIds[allRunIds.length - 1];
    }
}
