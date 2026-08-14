// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title  AgentIdentityRegistryV2
 * @notice The "silent substitution" detector — proves the agent that ran a
 *         task is the agent that was registered.
 *
 * Deliberately gates registerAgent()/revokeAgent() behind DEFAULT_ADMIN_ROLE
 * rather than ANCHOR_ROLE: establishing what counts as "the real agent" is
 * a higher-trust action than routine batch anchoring, and a compromised
 * anchor-worker hot key must not be able to register a fake identity or
 * revoke a real one. See AgentAuditLogV2 for the role-separation rationale
 * shared across all three V2 contracts.
 */
contract AgentIdentityRegistryV2 is AccessControl {
    struct AgentRecord {
        string agentId;
        bytes32 codeHash;
        address registeredBy;
        uint256 registeredAt;
        bool isActive;
        string modelName;
        string modelVersion;
    }

    struct VerificationResult {
        bool isValid;
        bool isActive;
        bool hashMatches;
        bytes32 storedHash;
        bytes32 providedHash;
        uint256 registeredAt;
        string agentId;
    }

    mapping(string => AgentRecord) public agentRecords;
    mapping(string => bytes32) public agentHashes;
    mapping(string => bool) public isRegistered;
    string[] public registeredAgentIds;

    // agentId is deliberately NOT `indexed`, unlike an earlier version of
    // this contract (and matching the same fix already applied to
    // TrustScoreRegistryV2.ScoreUpdated): Solidity only stores the
    // keccak256 HASH of an indexed dynamic type (string/bytes) in a log's
    // topics, not the value itself. An indexed agentId here would make
    // the human-readable id permanently unrecoverable from the event,
    // which breaks any future read model built on these events the same
    // way it would have broken rm_scores. registeredBy/revokedBy stay
    // indexed — address is a fixed-size type, so indexing it is free and
    // genuinely useful for "which admin did this" filtering.
    event AgentRegistered(string agentId, bytes32 codeHash, address indexed registeredBy, uint256 timestamp);
    event AgentUpdated(string agentId, bytes32 oldCodeHash, bytes32 newCodeHash, uint256 timestamp);
    event AgentRevoked(string agentId, address indexed revokedBy, uint256 timestamp);
    event IntegrityViolation(string agentId, bytes32 expectedHash, bytes32 providedHash, uint256 timestamp);

    modifier agentExists(string calldata agentId) {
        require(isRegistered[agentId], "AgentIdentityRegistryV2: agent not registered");
        _;
    }

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    function registerAgent(
        string calldata agentId,
        bytes32 codeHash,
        string calldata modelName,
        string calldata modelVersion
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(bytes(agentId).length > 0, "AgentIdentityRegistryV2: agentId cannot be empty");
        require(codeHash != bytes32(0), "AgentIdentityRegistryV2: codeHash cannot be zero");

        if (isRegistered[agentId]) {
            bytes32 oldHash = agentHashes[agentId];

            agentRecords[agentId].codeHash = codeHash;
            agentRecords[agentId].modelName = modelName;
            agentRecords[agentId].modelVersion = modelVersion;
            agentHashes[agentId] = codeHash;

            emit AgentUpdated(agentId, oldHash, codeHash, block.timestamp);
        } else {
            agentRecords[agentId] = AgentRecord({
                agentId: agentId,
                codeHash: codeHash,
                registeredBy: msg.sender,
                registeredAt: block.timestamp,
                isActive: true,
                modelName: modelName,
                modelVersion: modelVersion
            });

            agentHashes[agentId] = codeHash;
            isRegistered[agentId] = true;
            registeredAgentIds.push(agentId);

            emit AgentRegistered(agentId, codeHash, msg.sender, block.timestamp);
        }
    }

    function revokeAgent(string calldata agentId) external onlyRole(DEFAULT_ADMIN_ROLE) agentExists(agentId) {
        require(agentRecords[agentId].isActive, "AgentIdentityRegistryV2: agent already revoked");

        agentRecords[agentId].isActive = false;

        emit AgentRevoked(agentId, msg.sender, block.timestamp);
    }

    function verifyAgent(string calldata agentId, bytes32 currentHash) external view returns (bool) {
        if (!isRegistered[agentId]) return false;
        if (!agentRecords[agentId].isActive) return false;
        if (agentHashes[agentId] != currentHash) return false;
        return true;
    }

    /// @notice Same check as verifyAgent, but not `view` — can emit
    ///         IntegrityViolation on mismatch (the tamper alarm).
    function verifyAgentAndLog(string calldata agentId, bytes32 currentHash) external returns (bool) {
        if (!isRegistered[agentId] || !agentRecords[agentId].isActive) {
            return false;
        }

        bytes32 stored = agentHashes[agentId];
        if (stored != currentHash) {
            emit IntegrityViolation(agentId, stored, currentHash, block.timestamp);
            return false;
        }
        return true;
    }

    function verifyAgentFull(string calldata agentId, bytes32 currentHash)
        external
        view
        returns (VerificationResult memory result)
    {
        result.agentId = agentId;
        result.providedHash = currentHash;

        if (!isRegistered[agentId]) {
            return result;
        }

        AgentRecord storage rec = agentRecords[agentId];
        result.isActive = rec.isActive;
        result.storedHash = rec.codeHash;
        result.registeredAt = rec.registeredAt;
        result.hashMatches = (rec.codeHash == currentHash);
        result.isValid = rec.isActive && result.hashMatches;
    }

    function getAgent(string calldata agentId) external view agentExists(agentId) returns (AgentRecord memory) {
        return agentRecords[agentId];
    }

    function getAgentCount() external view returns (uint256) {
        return registeredAgentIds.length;
    }

    function getCodeHash(string calldata agentId) external view returns (bytes32) {
        return agentHashes[agentId];
    }
}
