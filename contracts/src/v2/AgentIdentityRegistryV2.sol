// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title  AgentIdentityRegistryV2
 * @notice The "silent substitution" detector — proves the agent that ran a
 *         task is the agent that was registered.
 *
 * registerAgent()/revokeAgent() are gated behind a DEDICATED REGISTRAR_ROLE
 * — not DEFAULT_ADMIN_ROLE, and not ANCHOR_ROLE either. Two things are
 * both true and need separate roles to hold at once:
 *   1. Establishing what counts as "the real agent" is a higher-trust
 *      action than routine batch anchoring — a compromised anchor-worker
 *      hot key must not be able to register a fake identity or revoke a
 *      real one, so this is NOT ANCHOR_ROLE.
 *   2. In a multi-tenant, self-serve deployment (the platform's actual
 *      operating model — see the plan's SDK strategy, "any third-party
 *      agent can be audited through a published SDK"), agent registration
 *      is a routine, automated action the backend performs on a tenant's
 *      behalf every time they call `register_agent()` — it cannot require
 *      a multisig signature ceremony per tenant onboarding, so this is
 *      NOT DEFAULT_ADMIN_ROLE either (an earlier version of this contract
 *      gated registration behind DEFAULT_ADMIN_ROLE directly, which was
 *      fine for a single-tenant demo but doesn't scale to that model).
 * REGISTRAR_ROLE is granted to the platform backend's own signer at
 * deploy time (see script/DeployV2.s.sol) — revocable independently of
 * ANCHOR_ROLE, and still ultimately governed by DEFAULT_ADMIN_ROLE (the
 * multisig), which alone can grant or revoke it. See AgentAuditLogV2 for
 * the role-separation rationale shared across all three V2 contracts.
 *
 * PROJECT NAMESPACING (plan §14.2's isolation strategy, "Namespaced agent
 * IDs — (project_id, agent_id) is the composite key, so two tenants may
 * both register an agent named researcher without collision"): every
 * external function takes `projectId` and internally keys ALL state off
 * keccak256(projectId, agentId), never off agentId alone. Before this,
 * the registry was keyed by bare agentId globally — two different
 * tenants both registering "researcher" would have SILENTLY COLLIDED,
 * the second registerAgent() call updating (AgentUpdated, not a fresh
 * AgentRegistered) the FIRST tenant's record, and the backend's own
 * per-project auth (main.py's principal.project_id) provided no
 * protection at all against this once the call reached the chain — the
 * isolation this platform's whole multi-tenancy design depends on
 * (invariant I7) simply didn't exist at this one layer. The trust-boundary
 * consequence was real, not theoretical: Tenant B registering an agent
 * named "researcher" AFTER Tenant A did would silently overwrite Tenant
 * A's stored codeHash, so Tenant A's subsequent verifyAgent() calls would
 * start comparing against TENANT B'S hash — a genuine cross-tenant
 * identity-substitution vulnerability, the exact failure mode this
 * contract exists to detect, just introduced from the isolation gap
 * rather than a compromised agent.
 */
contract AgentIdentityRegistryV2 is AccessControl {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");

    struct AgentRecord {
        uint256 projectId;
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

    mapping(bytes32 => AgentRecord) public agentRecords;
    mapping(bytes32 => bytes32) public agentHashes;
    mapping(bytes32 => bool) public isRegistered;
    // Every (projectId, agentId) pair ever registered, across all
    // tenants — purely informational (getAgentCount(), test/debugging
    // use only; nothing in the backend reads this), so it staying a
    // flat cross-tenant list rather than per-project is deliberate, not
    // an isolation gap the way the mappings above would have been.
    bytes32[] public registeredKeys;

    // agentId is deliberately NOT `indexed`, unlike an earlier version of
    // this contract (and matching the same fix already applied to
    // TrustScoreRegistryV2.ScoreUpdated): Solidity only stores the
    // keccak256 HASH of an indexed dynamic type (string/bytes) in a log's
    // topics, not the value itself. An indexed agentId here would make
    // the human-readable id permanently unrecoverable from the event,
    // which breaks any future read model built on these events the same
    // way it would have broken rm_scores. registeredBy/revokedBy stay
    // indexed — address is a fixed-size type, so indexing it is free and
    // genuinely useful for "which admin did this" filtering. projectId is
    // also indexed — fixed-size, and exactly the field a per-tenant
    // indexer/read-model needs to filter a log stream on cheaply.
    event AgentRegistered(
        uint256 indexed projectId, string agentId, bytes32 codeHash, address indexed registeredBy, uint256 timestamp
    );
    event AgentUpdated(
        uint256 indexed projectId, string agentId, bytes32 oldCodeHash, bytes32 newCodeHash, uint256 timestamp
    );
    event AgentRevoked(uint256 indexed projectId, string agentId, address indexed revokedBy, uint256 timestamp);
    event IntegrityViolation(
        uint256 indexed projectId, string agentId, bytes32 expectedHash, bytes32 providedHash, uint256 timestamp
    );

    modifier agentExists(uint256 projectId, string calldata agentId) {
        require(isRegistered[_key(projectId, agentId)], "AgentIdentityRegistryV2: agent not registered");
        _;
    }

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    function _key(uint256 projectId, string calldata agentId) internal pure returns (bytes32) {
        return keccak256(abi.encode(projectId, agentId));
    }

    function registerAgent(
        uint256 projectId,
        string calldata agentId,
        bytes32 codeHash,
        string calldata modelName,
        string calldata modelVersion
    ) external onlyRole(REGISTRAR_ROLE) {
        require(bytes(agentId).length > 0, "AgentIdentityRegistryV2: agentId cannot be empty");
        require(codeHash != bytes32(0), "AgentIdentityRegistryV2: codeHash cannot be zero");

        bytes32 key = _key(projectId, agentId);

        if (isRegistered[key]) {
            bytes32 oldHash = agentHashes[key];

            agentRecords[key].codeHash = codeHash;
            agentRecords[key].modelName = modelName;
            agentRecords[key].modelVersion = modelVersion;
            agentHashes[key] = codeHash;

            emit AgentUpdated(projectId, agentId, oldHash, codeHash, block.timestamp);
        } else {
            agentRecords[key] = AgentRecord({
                projectId: projectId,
                agentId: agentId,
                codeHash: codeHash,
                registeredBy: msg.sender,
                registeredAt: block.timestamp,
                isActive: true,
                modelName: modelName,
                modelVersion: modelVersion
            });

            agentHashes[key] = codeHash;
            isRegistered[key] = true;
            registeredKeys.push(key);

            emit AgentRegistered(projectId, agentId, codeHash, msg.sender, block.timestamp);
        }
    }

    function revokeAgent(uint256 projectId, string calldata agentId)
        external
        onlyRole(REGISTRAR_ROLE)
        agentExists(projectId, agentId)
    {
        bytes32 key = _key(projectId, agentId);
        require(agentRecords[key].isActive, "AgentIdentityRegistryV2: agent already revoked");

        agentRecords[key].isActive = false;

        emit AgentRevoked(projectId, agentId, msg.sender, block.timestamp);
    }

    function verifyAgent(uint256 projectId, string calldata agentId, bytes32 currentHash) external view returns (bool) {
        bytes32 key = _key(projectId, agentId);
        if (!isRegistered[key]) return false;
        if (!agentRecords[key].isActive) return false;
        if (agentHashes[key] != currentHash) return false;
        return true;
    }

    /// @notice Same check as verifyAgent, but not `view` — can emit
    ///         IntegrityViolation on mismatch (the tamper alarm).
    function verifyAgentAndLog(uint256 projectId, string calldata agentId, bytes32 currentHash)
        external
        returns (bool)
    {
        bytes32 key = _key(projectId, agentId);
        if (!isRegistered[key] || !agentRecords[key].isActive) {
            return false;
        }

        bytes32 stored = agentHashes[key];
        if (stored != currentHash) {
            emit IntegrityViolation(projectId, agentId, stored, currentHash, block.timestamp);
            return false;
        }
        return true;
    }

    function verifyAgentFull(uint256 projectId, string calldata agentId, bytes32 currentHash)
        external
        view
        returns (VerificationResult memory result)
    {
        result.agentId = agentId;
        result.providedHash = currentHash;

        bytes32 key = _key(projectId, agentId);
        if (!isRegistered[key]) {
            return result;
        }

        AgentRecord storage rec = agentRecords[key];
        result.isActive = rec.isActive;
        result.storedHash = rec.codeHash;
        result.registeredAt = rec.registeredAt;
        result.hashMatches = (rec.codeHash == currentHash);
        result.isValid = rec.isActive && result.hashMatches;
    }

    function getAgent(uint256 projectId, string calldata agentId)
        external
        view
        agentExists(projectId, agentId)
        returns (AgentRecord memory)
    {
        return agentRecords[_key(projectId, agentId)];
    }

    function getAgentCount() external view returns (uint256) {
        return registeredKeys.length;
    }

    function getCodeHash(uint256 projectId, string calldata agentId) external view returns (bytes32) {
        return agentHashes[_key(projectId, agentId)];
    }
}
