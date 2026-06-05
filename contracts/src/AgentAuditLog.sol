// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title AgentAuditLog
/// @notice Immutable on-chain black box — every AI agent action permanently recorded
/// @dev Deploy AFTER TrustScoreRegistry and AgentIdentityRegistry
contract AgentAuditLog {

    // ─────────────────────────────────────────────
    // STRUCT
    // ─────────────────────────────────────────────

    struct ActionRecord {
        string  runId;        // "run-a3f9c2b1" — groups all steps of one demo run
        string  agentId;      // "researcher" | "validator" | "scorer" | "reporter"
        string  action;       // "SEARCH" | "FACTCHECK" | "SCORE" | "REPORT"
        bytes32 inputHash;    // keccak256 of THIS agent's actual input
        bytes32 outputHash;   // keccak256 of THIS agent's actual output
        uint256 timestamp;    // block.timestamp — set by Monad, cannot be faked
        uint256 stepIndex;    // 0 | 1 | 2 | 3
        string  metadata;     // optional JSON string — human readable summary
        address txSender;     // msg.sender — auto set, proves wallet origin
    }

    // ─────────────────────────────────────────────
    // STATE
    // ─────────────────────────────────────────────

    address public owner;

    // All records ever logged — append only
    ActionRecord[] public records;

    // runId → array of record indices (so you can fetch all steps of one run)
    mapping(string => uint256[]) public runRecords;

    // Total action count per agent across all runs
    mapping(string => uint256) public agentActionCount;

    // ─────────────────────────────────────────────
    // EVENTS
    // ─────────────────────────────────────────────

    event ActionLogged(
        string  indexed runId,
        string  indexed agentId,
        string          action,
        uint256         stepIndex,
        bytes32         inputHash,
        bytes32         outputHash,
        uint256         timestamp,
        uint256         recordIndex  // position in records[] array
    );

    // ─────────────────────────────────────────────
    // ERRORS
    // ─────────────────────────────────────────────

    error OnlyOwner();
    error EmptyRunId();
    error EmptyAgentId();
    error InvalidAction();

    // ─────────────────────────────────────────────
    // CONSTRUCTOR
    // ─────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
    }

    // ─────────────────────────────────────────────
    // MODIFIERS
    // ─────────────────────────────────────────────

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    // ─────────────────────────────────────────────
    // CORE FUNCTION
    // ─────────────────────────────────────────────

    /// @notice Log one agent action permanently on-chain
    /// @param runId      Unique run identifier e.g. "run-a3f9c2b1"
    /// @param agentId    Agent name: "researcher" | "validator" | "scorer" | "reporter"
    /// @param action     Action vocab: "SEARCH" | "FACTCHECK" | "SCORE" | "REPORT"
    /// @param inputHash  keccak256 of this agent's actual input string
    /// @param outputHash keccak256 of this agent's actual output string
    /// @param stepIndex  Position in pipeline: 0 | 1 | 2 | 3
    /// @param metadata   Optional JSON string for human readable context
    function logAction(
        string  calldata runId,
        string  calldata agentId,
        string  calldata action,
        bytes32          inputHash,
        bytes32          outputHash,
        uint256          stepIndex,
        string  calldata metadata
    ) external onlyOwner returns (uint256 recordIndex) {

        // Basic validation
        if (bytes(runId).length    == 0) revert EmptyRunId();
        if (bytes(agentId).length  == 0) revert EmptyAgentId();
        if (bytes(action).length   == 0) revert InvalidAction();

        // Build the record
        ActionRecord memory record = ActionRecord({
            runId:       runId,
            agentId:     agentId,
            action:      action,
            inputHash:   inputHash,
            outputHash:  outputHash,
            timestamp:   block.timestamp,
            stepIndex:   stepIndex,
            metadata:    metadata,
            txSender:    msg.sender
        });

        // Append to master list
        records.push(record);
        recordIndex = records.length - 1;

        // Index by runId so Python can fetch "all steps of run-a3f9c2b1"
        runRecords[runId].push(recordIndex);

        // Track per-agent action count
        agentActionCount[agentId]++;

        emit ActionLogged(
            runId,
            agentId,
            action,
            stepIndex,
            inputHash,
            outputHash,
            block.timestamp,
            recordIndex
        );
    }

    // ─────────────────────────────────────────────
    // READ FUNCTIONS (called by FastAPI GET /audit-log)
    // ─────────────────────────────────────────────

    /// @notice Get total number of records ever logged
    function getTotalRecords() external view returns (uint256) {
        return records.length;
    }

    /// @notice Get all record indices belonging to a specific run
    /// @dev Python calls this first, then fetches each record by index
    function getRunRecordIndices(string calldata runId)
        external view returns (uint256[] memory)
    {
        return runRecords[runId];
    }

    /// @notice Get a single record by its index in the master array
    function getRecord(uint256 index)
        external view
        returns (ActionRecord memory)
    {
        return records[index];
    }

    /// @notice Get multiple records at once — saves RPC round trips
    /// @dev Pass indices from getRunRecordIndices()
    function getRecordsBatch(uint256[] calldata indices)
        external view
        returns (ActionRecord[] memory batch)
    {
        batch = new ActionRecord[](indices.length);
        for (uint256 i = 0; i < indices.length; i++) {
            batch[i] = records[indices[i]];
        }
    }

    /// @notice Verify integrity — recompute and compare hashes
    /// @dev Called by POST /verify endpoint in FastAPI
    /// @param index       Record index to verify
    /// @param rawInput    Original input string to rehash
    /// @param rawOutput   Original output string to rehash
    function verifyRecord(
        uint256 index,
        string  calldata rawInput,
        string  calldata rawOutput
    ) external view returns (bool inputMatch, bool outputMatch) {
        ActionRecord memory record = records[index];
        inputMatch  = (keccak256(bytes(rawInput))  == record.inputHash);
        outputMatch = (keccak256(bytes(rawOutput)) == record.outputHash);
    }
}
