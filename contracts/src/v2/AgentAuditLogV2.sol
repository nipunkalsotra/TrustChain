// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/**
 * @title  AgentAuditLogV2
 * @notice Batched, tamper-evident audit anchor for AI agent steps.
 *
 * V1 wrote one transaction per agent step (~13 tx/run) and stored full
 * `string` fields in contract storage (the most expensive resource on an
 * EVM chain). V2 anchors an entire batch of steps as a single Merkle root
 * per transaction, with per-step verifiability preserved via inclusion
 * proofs — see backend/blockchain/merkle.py for the exact tree
 * construction this contract's proof verification must match.
 *
 * TRUST MODEL
 * -----------
 * V1 had one owner key with unlimited authority — a compromised key could
 * forge "immutable" records or revoke any agent. V2 separates roles:
 *   - ANCHOR_ROLE:        may call anchorBatch(). Held by the anchor
 *                          worker's hot signing key. Cannot administer
 *                          anything else — see contracts/test for a test
 *                          that proves this role boundary holds.
 *   - DEFAULT_ADMIN_ROLE:  may grant/revoke roles and pause/unpause.
 *                          Intended for a multisig, not a hot wallet.
 *
 * LEAF ENCODING (must match backend/blockchain/merkle.py exactly)
 * -----------------------------------------------------------------
 * leaf = keccak256(keccak256(abi.encode(
 *     runIdHash, agentIdHash, actionHash, inputHash, outputHash,
 *     uint64(stepIndex), uint64(timestamp)
 * )))
 *
 * Double-hashing the leaf is a deliberate defense, not redundancy: if a
 * leaf were a *single* hash of its preimage, an attacker could craft a
 * "leaf" whose preimage happens to equal some real internal node's two
 * 32-byte children — abi.encode(bytes32,bytes32) is exactly the 64-byte
 * concatenation the tree's pair-hashing uses, so that "forged leaf" would
 * match a real internal node for free, letting the attacker reuse a real
 * sibling path to prove membership for data that was never anchored.
 * Double-hashing requires a genuine keccak preimage, not a structural
 * coincidence, closing that off.
 */
contract AgentAuditLogV2 is AccessControl, Pausable {
    bytes32 public constant ANCHOR_ROLE = keccak256("ANCHOR_ROLE");

    struct BatchRecord {
        bytes32 runIdHash;
        bytes32 merkleRoot;
        uint32 stepCount;
        uint256 timestamp;
        address anchoredBy;
    }

    /// @notice All anchored batches, in submission order.
    BatchRecord[] public batches;

    /// @notice runIdHash -> indices into `batches` — a run may span more
    ///         than one batch if its steps land on either side of a flush.
    mapping(bytes32 => uint256[]) public batchesForRun;

    /// @notice Replay/duplicate-submission guard. At-least-once delivery
    ///         from the anchor worker (crash-and-resubmit) must have
    ///         exactly-once effect on-chain.
    mapping(bytes32 => bool) public usedRoots;

    /// @dev event data is ~10x cheaper than storage (§12.3) — metaURI
    ///      lives ONLY in the log, never in `BatchRecord`/contract storage.
    event BatchAnchored(
        uint256 indexed anchorId,
        bytes32 indexed runIdHash,
        bytes32 merkleRoot,
        uint32 stepCount,
        string metaURI,
        uint256 timestamp,
        address anchoredBy
    );

    error EmptyBatch();
    error DuplicateRoot(bytes32 merkleRoot);
    error MetaURITooLong();

    /// @dev Input length cap to prevent gas-griefing through oversized
    ///      strings (§11.6) — generous enough for any real IPFS/Arweave
    ///      URI (ipfs://<CID>, ar://<txid>, or a gateway URL) with margin.
    uint256 public constant MAX_META_URI_LENGTH = 256;

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    /**
     * @notice Anchor a batch of agent steps as a single Merkle root.
     * @dev    Called by the anchor worker, never directly by a user. The
     *         worker computes `merkleRoot` off-chain over the batch's leaf
     *         hashes (backend/blockchain/merkle.py) and persists the exact
     *         leaf set + order in Postgres before calling this — the root
     *         can only be reproduced from that recorded order, so it's
     *         written once here and never needs to change.
     * @param runIdHash   keccak256(runId) — batches may be per-run or span
     *                    multiple runs; runIdHash is metadata for
     *                    filtering, not a batching boundary this contract
     *                    enforces.
     * @param merkleRoot  Root of the batch's leaf hashes.
     * @param stepCount   Number of leaves in the tree — informational, lets
     *                    a reader sanity-check a proof's claimed position
     *                    without re-deriving the whole tree.
     * @param metaURI     Optional content-address (e.g. "ipfs://<CID>",
     *                    "ar://<txid>") for the batch's full leaf set,
     *                    pinned off-chain by the anchor worker. Pass "" to
     *                    omit. This is §8.1's data-availability mitigation:
     *                    a Merkle root alone proves a leaf existed *if the
     *                    off-chain leaf set survives* — pinning that set
     *                    to IPFS/Arweave and recording the CID here
     *                    restores full public verifiability independent of
     *                    TrustChain's own database staying intact. Lives
     *                    only in the event log, never in contract storage
     *                    (see BatchAnchored's own docstring).
     */
    function anchorBatch(bytes32 runIdHash, bytes32 merkleRoot, uint32 stepCount, string calldata metaURI)
        external
        onlyRole(ANCHOR_ROLE)
        whenNotPaused
        returns (uint256 anchorId)
    {
        if (stepCount == 0) revert EmptyBatch();
        if (usedRoots[merkleRoot]) revert DuplicateRoot(merkleRoot);
        if (bytes(metaURI).length > MAX_META_URI_LENGTH) revert MetaURITooLong();

        usedRoots[merkleRoot] = true;

        batches.push(
            BatchRecord({
                runIdHash: runIdHash,
                merkleRoot: merkleRoot,
                stepCount: stepCount,
                timestamp: block.timestamp,
                anchoredBy: msg.sender
            })
        );
        anchorId = batches.length - 1;
        batchesForRun[runIdHash].push(anchorId);

        emit BatchAnchored(anchorId, runIdHash, merkleRoot, stepCount, metaURI, block.timestamp, msg.sender);
    }

    /**
     * @notice Verify that `leaf` is a member of the Merkle tree anchored
     *         as `anchorId`, using OpenZeppelin's standard sorted-pair
     *         verifier — the same algorithm backend/blockchain/merkle.py
     *         implements for proof generation.
     */
    function verifyProof(uint256 anchorId, bytes32 leaf, bytes32[] calldata proof) external view returns (bool) {
        bytes32 root = batches[anchorId].merkleRoot;
        return MerkleProof.verify(proof, root, leaf);
    }

    function getBatch(uint256 anchorId) external view returns (BatchRecord memory) {
        return batches[anchorId];
    }

    function getTotalBatches() external view returns (uint256) {
        return batches.length;
    }

    function getBatchesForRun(bytes32 runIdHash) external view returns (uint256[] memory) {
        return batchesForRun[runIdHash];
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }
}
