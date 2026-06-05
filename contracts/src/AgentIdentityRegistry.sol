// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  AgentIdentityRegistry
 * @notice Contract 3 of TrustChain — the "silent substitution" detector.
 *
 * PURPOSE
 * -------
 * Prove that the agent which ran a task is exactly the agent that was
 * registered at deploy time.  If anyone swaps the Researcher agent for a
 * different model / prompt, the codeHash changes and verifyAgent() returns
 * false — visible to the judge in real time.
 *
 * CODEHASH DECISION (from design doc — "most critical decision")
 * --------------------------------------------------------------
 * We use keccak256 of the agent config dict because it captures:
 *   - agent name / role
 *   - model name  (e.g. "gpt-4o")
 *   - model version
 *   - system prompt (full text)
 *   - temperature + any other config flags
 *
 * Both registerAgent() at startup AND the Verify button in the UI must
 * compute the hash from this same config dict — never change the schema.
 *
 * REGISTRATION TIMING
 * -------------------
 * Agents are registered ONCE via a deploy script before the demo starts
 * (hardcoded approach — cleaner for hackathon).  FastAPI reads the four
 * registered agentIds from .env and never re-registers unless you run
 * the script again.
 *
 * VERIFY INTEGRITY BUTTON
 * -----------------------
 * Checks BOTH:
 *   1. The agent codeHash still matches what was registered on-chain
 *   2. Re-derives the hash from the live config dict and compares
 * Returns a structured result so the frontend can show a detailed breakdown.
 */

contract AgentIdentityRegistry {

    // ─────────────────────────────────────────────
    // STRUCTS
    // ─────────────────────────────────────────────

    /**
     * @dev Full record stored for each registered agent.
     *      Stored in agentRecords mapping, looked up by agentId string.
     */
    struct AgentRecord {
        string   agentId;          // e.g. "researcher", "validator", "scorer", "reporter"
        bytes32  codeHash;         // keccak256 of the agent config dict (see above)
        address  registeredBy;     // wallet that called registerAgent() — always owner
        uint256  registeredAt;     // block.timestamp at registration time
        bool     isActive;         // false = revoked, verifyAgent() will return false
        string   modelName;        // stored for UI display only, e.g. "gpt-4o"
        string   modelVersion;     // stored for UI display only, e.g. "2024-11-20"
    }

    /**
     * @dev Return type for verifyAgentFull() — gives the frontend
     *      enough detail to render the "Verify Integrity" panel.
     */
    struct VerificationResult {
        bool    isValid;            // true only if active AND hashes match
        bool    isActive;           // false if agent was revoked
        bool    hashMatches;        // false if codeHash has changed
        bytes32 storedHash;         // what was registered on-chain
        bytes32 providedHash;       // what the caller computed right now
        uint256 registeredAt;       // when agent was first registered
        string  agentId;
    }


    // ─────────────────────────────────────────────
    // STATE VARIABLES
    // ─────────────────────────────────────────────

    /// @notice Contract owner — the wallet that deployed this contract.
    ///         Only owner can register or revoke agents.
    address public owner;

    /// @notice agentId  →  AgentRecord
    ///         Primary lookup: given "researcher", return full record.
    mapping(string => AgentRecord) public agentRecords;

    /// @notice agentId  →  codeHash
    ///         Separate lightweight mapping for cheap hash-only reads.
    ///         Used by verifyAgent() so it doesn't load the full struct.
    mapping(string => bytes32) public agentHashes;

    /// @notice agentId  →  bool (has this agentId ever been registered?)
    ///         Needed because Solidity mappings return zero values for
    ///         non-existent keys — we must distinguish "never registered"
    ///         from "registered with hash 0x000...".
    mapping(string => bool) public isRegistered;

    /// @notice List of all registered agentIds — for iteration in the UI.
    string[] public registeredAgentIds;


    // ─────────────────────────────────────────────
    // EVENTS
    // ─────────────────────────────────────────────

    /**
     * @dev Emitted when a new agent is registered.
     *      Web3.py listens for this to confirm startup registration.
     *      Indexed fields: agentId and registeredBy — most common filters.
     */
    event AgentRegistered(
        string  indexed agentId,
        bytes32         codeHash,
        address indexed registeredBy,
        uint256         timestamp
    );

    /**
     * @dev Emitted when an agent's codeHash is updated (re-registration).
     *      Lets you track the full history of hash changes on-chain.
     */
    event AgentUpdated(
        string  indexed agentId,
        bytes32         oldCodeHash,
        bytes32         newCodeHash,
        uint256         timestamp
    );

    /**
     * @dev Emitted when an agent is revoked.
     *      After this, verifyAgent() returns false for this agentId.
     */
    event AgentRevoked(
        string  indexed agentId,
        address indexed revokedBy,
        uint256         timestamp
    );

    /**
     * @dev Emitted when verifyAgent() is called with a mismatching hash.
     *      This is the tamper alarm — FastAPI can listen for this event
     *      and push an alert to the frontend immediately.
     */
    event IntegrityViolation(
        string  indexed agentId,
        bytes32         expectedHash,    // what was registered
        bytes32         providedHash,    // what was just computed
        uint256         timestamp
    );


    // ─────────────────────────────────────────────
    // MODIFIERS
    // ─────────────────────────────────────────────

    /**
     * @dev Restricts function to the contract owner (deployer wallet).
     *      msg.sender is the address that signed the transaction.
     *      Reverts with a clear message so Web3.py can surface the error.
     */
    modifier onlyOwner() {
        require(
            msg.sender == owner,
            "AgentIdentityRegistry: caller is not the owner"
        );
        _;
    }

    /**
     * @dev Ensures the agentId has been registered before performing
     *      lookups.  Prevents silent failures on typos in agentId.
     */
    modifier agentExists(string calldata agentId) {
        require(
            isRegistered[agentId],
            "AgentIdentityRegistry: agent not registered"
        );
        _;
    }


    // ─────────────────────────────────────────────
    // CONSTRUCTOR
    // ─────────────────────────────────────────────

    /**
     * @dev Sets the deployer as owner.
     *      Called once when you run: npx hardhat run scripts/deploy.js
     *      msg.sender at deploy time = your wallet address from .env PRIVATE_KEY
     */
    constructor() {
        owner = msg.sender;
    }


    // ─────────────────────────────────────────────
    // WRITE FUNCTIONS  (cost gas — called by FastAPI via Web3.py)
    // ─────────────────────────────────────────────

    /**
     * @notice Register an agent with its identity fingerprint.
     * @dev    Called ONCE per agent via the deploy script before the demo.
     *         If the agent was already registered, this updates the codeHash
     *         and emits AgentUpdated instead of AgentRegistered.
     *
     * @param agentId      Human-readable ID: "researcher", "validator", etc.
     * @param codeHash     keccak256 of the agent config dict (computed in Python)
     * @param modelName    e.g. "gpt-4o"   — for display only, not used in verification
     * @param modelVersion e.g. "2024-11-20" — for display only
     *
     * Python side (Web3.py):
     *   config = {"agentId": "researcher", "model": "gpt-4o", "version": "...", "prompt": "..."}
     *   code_hash = Web3.keccak(text=json.dumps(config, sort_keys=True))
     *   contract.functions.registerAgent("researcher", code_hash, "gpt-4o", "2024-11-20")
     *           .build_transaction({...})
     */
    function registerAgent(
        string  calldata agentId,
        bytes32          codeHash,
        string  calldata modelName,
        string  calldata modelVersion
    ) external onlyOwner {

        require(bytes(agentId).length > 0,   "AgentIdentityRegistry: agentId cannot be empty");
        require(codeHash != bytes32(0),        "AgentIdentityRegistry: codeHash cannot be zero");

        if (isRegistered[agentId]) {
            // Agent exists — update the codeHash and emit AgentUpdated
            bytes32 oldHash = agentHashes[agentId];

            agentRecords[agentId].codeHash     = codeHash;
            agentRecords[agentId].modelName    = modelName;
            agentRecords[agentId].modelVersion = modelVersion;
            agentHashes[agentId]               = codeHash;

            emit AgentUpdated(agentId, oldHash, codeHash, block.timestamp);

        } else {
            // New agent — create full record
            agentRecords[agentId] = AgentRecord({
                agentId:        agentId,
                codeHash:       codeHash,
                registeredBy:   msg.sender,
                registeredAt:   block.timestamp,
                isActive:       true,
                modelName:      modelName,
                modelVersion:   modelVersion
            });

            agentHashes[agentId]    = codeHash;
            isRegistered[agentId]   = true;
            registeredAgentIds.push(agentId);

            emit AgentRegistered(agentId, codeHash, msg.sender, block.timestamp);
        }
    }

    /**
     * @notice Revoke an agent — marks it inactive.
     * @dev    After revoking, verifyAgent() returns false for this agentId.
     *         Use this if an agent is compromised during the demo.
     *         Revocation is permanent — you must re-register to re-activate.
     *
     * @param agentId  The agent to revoke.
     */
    function revokeAgent(
        string calldata agentId
    ) external onlyOwner agentExists(agentId) {

        require(
            agentRecords[agentId].isActive,
            "AgentIdentityRegistry: agent already revoked"
        );

        agentRecords[agentId].isActive = false;

        emit AgentRevoked(agentId, msg.sender, block.timestamp);
    }


    // ─────────────────────────────────────────────
    // READ / VIEW FUNCTIONS  (free — no gas)
    // ─────────────────────────────────────────────

    /**
     * @notice Fast boolean check — is this agent still the registered one?
     * @dev    Called by AgentAuditLog BEFORE every agent invocation in LangGraph.
     *         Cheap: only reads agentHashes mapping (no struct load).
     *         Also emits IntegrityViolation if the hash doesn't match —
     *         Web3.py event listener catches this and alerts the frontend.
     *
     *         NOTE: view functions cannot emit events (events require state change).
     *         So the event emission is in verifyAgentAndLog() below.
     *         Use this for pure boolean checks, verifyAgentAndLog() for full flow.
     *
     * @param agentId       The agent being verified.
     * @param currentHash   keccak256 of the agent config dict, computed right now in Python.
     * @return bool         true = agent is legit. false = tampered or revoked.
     */
    function verifyAgent(
        string  calldata agentId,
        bytes32          currentHash
    ) external view returns (bool) {

        if (!isRegistered[agentId])               return false;
        if (!agentRecords[agentId].isActive)      return false;
        if (agentHashes[agentId] != currentHash)  return false;

        return true;
    }

    /**
     * @notice Full verification with event emission — use this in the main flow.
     * @dev    Same logic as verifyAgent() but NOT a view function so it can
     *         emit IntegrityViolation when tampering is detected.
     *         Called by FastAPI's /verify endpoint (the "Verify Integrity" button).
     *
     * @param agentId       The agent being verified.
     * @param currentHash   keccak256 of live config dict.
     * @return bool         true = all clear.
     */
    function verifyAgentAndLog(
        string  calldata agentId,
        bytes32          currentHash
    ) external returns (bool) {

        if (!isRegistered[agentId] || !agentRecords[agentId].isActive) {
            return false;
        }

        bytes32 stored = agentHashes[agentId];

        if (stored != currentHash) {
            // Emit the tamper alarm — Web3.py event listener picks this up
            emit IntegrityViolation(agentId, stored, currentHash, block.timestamp);
            return false;
        }

        return true;
    }

    /**
     * @notice Full verification result — powers the "Verify Integrity" panel in UI.
     * @dev    Returns VerificationResult struct so the frontend can render
     *         a detailed breakdown: which hash was stored, which was computed,
     *         whether the agent is active, and whether they match.
     *         This is what makes the judge's "wow moment".
     *
     * @param agentId      The agent to check.
     * @param currentHash  keccak256 of the live config dict from Python.
     * @return result      VerificationResult struct — decoded by Web3.py.
     */
    function verifyAgentFull(
        string  calldata agentId,
        bytes32          currentHash
    ) external view returns (VerificationResult memory result) {

        result.agentId       = agentId;
        result.providedHash  = currentHash;

        if (!isRegistered[agentId]) {
            result.isValid      = false;
            result.isActive     = false;
            result.hashMatches  = false;
            return result;
        }

        AgentRecord storage rec = agentRecords[agentId];

        result.isActive        = rec.isActive;
        result.storedHash      = rec.codeHash;
        result.registeredAt    = rec.registeredAt;
        result.hashMatches     = (rec.codeHash == currentHash);
        result.isValid         = rec.isActive && result.hashMatches;

        return result;
    }

    /**
     * @notice Get full record for a given agentId.
     * @dev    Used by the Trust Score panel in the frontend to show
     *         registration metadata: who registered, when, which model.
     *
     * @param agentId  The agent to look up.
     * @return AgentRecord struct — all fields.
     */
    function getAgent(
        string calldata agentId
    ) external view agentExists(agentId) returns (AgentRecord memory) {
        return agentRecords[agentId];
    }

    /**
     * @notice Returns how many distinct agents have been registered.
     * @dev    Used by the frontend to iterate registeredAgentIds[].
     */
    function getAgentCount() external view returns (uint256) {
        return registeredAgentIds.length;
    }

    /**
     * @notice Returns the stored codeHash for an agent.
     * @dev    Lightweight read — used by AgentAuditLog before calling verifyAgent().
     *
     * @param agentId  The agent to look up.
     * @return bytes32  Stored codeHash, or bytes32(0) if not registered.
     */
    function getCodeHash(
        string calldata agentId
    ) external view returns (bytes32) {
        return agentHashes[agentId];
    }
}
