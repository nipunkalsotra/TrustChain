// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title  TrustChainRegistry
 * @notice Version -> deployment-address resolution (plan §12.1/§12.4),
 *         so SDKs and indexers discover which AgentAuditLogV2/
 *         TrustScoreRegistryV2/AgentIdentityRegistryV2 addresses are
 *         current without hardcoding them.
 *
 * WHY THIS EXISTS: the audit log is deliberately non-upgradeable (§4.3 —
 * "an upgradeable audit log is a contradiction"). Evolution instead means
 * deploying a new generation of contracts and pointing at them here.
 * Historical proofs must never break (§12.4 migration strategy): once a
 * version is registered, its addresses are permanent — registerDeployment
 * reverts on a version that's already registered, matching the audit
 * log's own append-only ethos rather than allowing a version's meaning to
 * be silently redefined out from under anyone holding an old proof.
 *
 * `currentVersion` is a separate, mutable pointer — advancing it is how
 * "V2 is the new default" gets communicated to new callers, without
 * touching what version N's addresses actually were.
 */
contract TrustChainRegistry is AccessControl {
    struct Deployment {
        address auditLog;
        address trustScore;
        address identityRegistry;
        uint256 registeredAt;
    }

    /// @notice version -> deployment addresses. Immutable once set — see
    ///         registerDeployment's revert-on-overwrite behavior.
    mapping(uint256 => Deployment) private _deployments;

    /// @notice Which registered version new callers should resolve to by
    ///         default. 0 until the admin advances it past the initial
    ///         "unset" state.
    uint256 public currentVersion;

    event DeploymentRegistered(
        uint256 indexed version, address auditLog, address trustScore, address identityRegistry, uint256 timestamp
    );
    event CurrentVersionUpdated(uint256 indexed previousVersion, uint256 indexed newVersion);

    error VersionAlreadyRegistered(uint256 version);
    error VersionNotRegistered(uint256 version);
    error ZeroAddress();

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    /**
     * @notice Register the three contract addresses for a new generation.
     * @dev    One-time write per version — see this contract's own
     *         docstring for why. Governed by the multisig
     *         (DEFAULT_ADMIN_ROLE), the same authority that governs
     *         pausing/role management on the contracts themselves.
     */
    function registerDeployment(uint256 version, address auditLog, address trustScore, address identityRegistry)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_deployments[version].registeredAt != 0) revert VersionAlreadyRegistered(version);
        if (auditLog == address(0) || trustScore == address(0) || identityRegistry == address(0)) {
            revert ZeroAddress();
        }

        _deployments[version] = Deployment({
            auditLog: auditLog,
            trustScore: trustScore,
            identityRegistry: identityRegistry,
            registeredAt: block.timestamp
        });

        emit DeploymentRegistered(version, auditLog, trustScore, identityRegistry, block.timestamp);
    }

    /// @notice Advance the "current" pointer to an already-registered version.
    function setCurrentVersion(uint256 version) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_deployments[version].registeredAt == 0) revert VersionNotRegistered(version);
        uint256 previous = currentVersion;
        currentVersion = version;
        emit CurrentVersionUpdated(previous, version);
    }

    function getDeployment(uint256 version) external view returns (Deployment memory) {
        // slither-disable-next-line incorrect-equality
        if (_deployments[version].registeredAt == 0) revert VersionNotRegistered(version);
        return _deployments[version];
    }

    function getCurrentDeployment() external view returns (Deployment memory) {
        return this.getDeployment(currentVersion);
    }

    function isRegistered(uint256 version) external view returns (bool) {
        return _deployments[version].registeredAt != 0;
    }
}
