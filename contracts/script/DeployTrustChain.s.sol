// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "forge-std/console.sol";

import "../src/TrustScoreRegistry.sol";
import "../src/AgentIdentityRegistry.sol";
import "../src/AgentAuditLog.sol";

/// @title  DeployTrustChain
/// @notice Deploys all 3 TrustChain contracts in the correct order and
///         prints every address + the .env block you need for FastAPI.
///
/// DEPLOY ORDER (required):
///   1. TrustScoreRegistry    — no dependencies
///   2. AgentIdentityRegistry — no dependencies
///   3. AgentAuditLog         — no dependencies (Python calls contracts separately)
///
/// USAGE:
///   forge script script/DeployTrustChain.s.sol \
///     --rpc-url monad \
///     --private-key $PRIVATE_KEY \
///     --broadcast \
///     -vvvv
///
/// After running, copy the printed .env block into backend/.env
///
contract DeployTrustChain is Script {

    // ─────────────────────────────────────────────
    // AGENT REGISTRATION CONFIG
    // ─────────────────────────────────────────────
    // These are registered on-chain immediately after deployment.
    // Python must compute codeHash using the SAME config dict schema:
    //   config = { "agentId": "...", "model": "...", "version": "...", "systemPrompt": "..." }
    //   code_hash = Web3.keccak(text=json.dumps(config, sort_keys=True))
    //
    // IMPORTANT: These are placeholder hashes.
    // Replace them with real values AFTER you write your Python agents.
    // Run: python3 scripts/compute_hashes.py  (see printed instructions below)
    // ─────────────────────────────────────────────

    string  constant RESEARCHER_ID      = "researcher";
    string  constant RESEARCHER_MODEL   = "gpt-4o";
    string  constant RESEARCHER_VERSION = "2024-11-20";

    string  constant VALIDATOR_ID       = "validator";
    string  constant VALIDATOR_MODEL    = "gpt-4o";
    string  constant VALIDATOR_VERSION  = "2024-11-20";

    string  constant SCORER_ID          = "scorer";
    string  constant SCORER_MODEL       = "gpt-4o";
    string  constant SCORER_VERSION     = "2024-11-20";

    string  constant REPORTER_ID        = "reporter";
    string  constant REPORTER_MODEL     = "gpt-4o";
    string  constant REPORTER_VERSION   = "2024-11-20";

    // ─────────────────────────────────────────────
    // RUN
    // ─────────────────────────────────────────────

    function run() external {

        // Read PRIVATE_KEY from environment — never hardcode it
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployer           = vm.addr(deployerPrivateKey);

        console.log("========================================");
        console.log("  TrustChain Deployment");
        console.log("========================================");
        console.log("Deployer wallet:", deployer);
        console.log("Chain ID:       ", block.chainid);
        console.log("Block number:   ", block.number);
        console.log("");

        vm.startBroadcast(deployerPrivateKey);

        // ── STEP 1: TrustScoreRegistry ───────────────────────────────────
        console.log(">> [1/3] Deploying TrustScoreRegistry...");
        TrustScoreRegistry trustScore = new TrustScoreRegistry();
        console.log("   TrustScoreRegistry deployed at:", address(trustScore));

        // ── STEP 2: AgentIdentityRegistry ────────────────────────────────
        console.log(">> [2/3] Deploying AgentIdentityRegistry...");
        AgentIdentityRegistry identityRegistry = new AgentIdentityRegistry();
        console.log("   AgentIdentityRegistry deployed at:", address(identityRegistry));

        // ── STEP 3: AgentAuditLog ─────────────────────────────────────────
        console.log(">> [3/3] Deploying AgentAuditLog...");
        AgentAuditLog auditLog = new AgentAuditLog();
        console.log("   AgentAuditLog deployed at:", address(auditLog));

        // ── STEP 4: Register all 4 agents in AgentIdentityRegistry ───────
        // Uses placeholder hashes — replace these with real values from
        // python3 scripts/compute_hashes.py before the demo.
        console.log("");
        console.log(">> [4/4] Registering 4 agents in AgentIdentityRegistry...");

        // Read agent hashes from environment (set by compute_hashes.py output)
        // Falls back to a deterministic placeholder so deploy doesn't fail
        // if you haven't run compute_hashes.py yet.
        bytes32 researcherHash = _envHashOrPlaceholder("RESEARCHER_HASH", RESEARCHER_ID);
        bytes32 validatorHash  = _envHashOrPlaceholder("VALIDATOR_HASH",  VALIDATOR_ID);
        bytes32 scorerHash     = _envHashOrPlaceholder("SCORER_HASH",     SCORER_ID);
        bytes32 reporterHash   = _envHashOrPlaceholder("REPORTER_HASH",   REPORTER_ID);

        identityRegistry.registerAgent(
            RESEARCHER_ID, researcherHash, RESEARCHER_MODEL, RESEARCHER_VERSION
        );
        console.log("   Registered: researcher");

        identityRegistry.registerAgent(
            VALIDATOR_ID, validatorHash, VALIDATOR_MODEL, VALIDATOR_VERSION
        );
        console.log("   Registered: validator");

        identityRegistry.registerAgent(
            SCORER_ID, scorerHash, SCORER_MODEL, SCORER_VERSION
        );
        console.log("   Registered: scorer");

        identityRegistry.registerAgent(
            REPORTER_ID, reporterHash, REPORTER_MODEL, REPORTER_VERSION
        );
        console.log("   Registered: reporter");

        vm.stopBroadcast();

        // ── VERIFICATION: confirm owner is set correctly ──────────────────
        require(trustScore.owner()        == deployer, "TrustScore owner mismatch");
        require(identityRegistry.owner()  == deployer, "Identity owner mismatch");
        require(auditLog.owner()          == deployer, "AuditLog owner mismatch");
        require(identityRegistry.getAgentCount() == 4, "Agent count mismatch");

        // ── PRINT SUMMARY ─────────────────────────────────────────────────
        _printSummary(
            deployer,
            address(trustScore),
            address(identityRegistry),
            address(auditLog)
        );
    }

    // ─────────────────────────────────────────────
    // HELPERS
    // ─────────────────────────────────────────────

    /// @dev Try to read a bytes32 env var. If not set, derive a placeholder
    ///      from the agentId string so the deploy script doesn't revert.
    ///      Replace with real hashes before the demo by running compute_hashes.py
    function _envHashOrPlaceholder(
        string memory envKey,
        string memory agentId
    ) internal view returns (bytes32) {
        // Try reading from env — if not set, Foundry returns bytes32(0)
        bytes32 val = vm.envOr(envKey, bytes32(0));
        if (val != bytes32(0)) {
            return val;
        }
        // Derive a placeholder so deploy succeeds during testing
        console.log(
            string.concat(
                "   WARNING: ", envKey,
                " not set - using placeholder hash for ", agentId,
                ". Run compute_hashes.py before demo!"
            )
        );
        return keccak256(abi.encodePacked("PLACEHOLDER:", agentId));
    }

    /// @dev Print the full deployment summary + copy-paste .env block
    function _printSummary(
        address deployer,
        address trustScore,
        address identityRegistry,
        address auditLog
    ) internal view {

        string memory monadBase = "https://testnet.monadexplorer.com/address/";

        console.log("");
        console.log("========================================");
        console.log("  DEPLOYMENT COMPLETE");
        console.log("========================================");
        console.log("");
        console.log("Contract Addresses:");
        console.log("  TrustScoreRegistry   :", trustScore);
        console.log("  AgentIdentityRegistry:", identityRegistry);
        console.log("  AgentAuditLog        :", auditLog);
        console.log("");
        console.log("Monad Explorer Links:");
        console.log(string.concat("  TrustScore    : ", monadBase, _addrToString(trustScore)));
        console.log(string.concat("  Identity      : ", monadBase, _addrToString(identityRegistry)));
        console.log(string.concat("  AuditLog      : ", monadBase, _addrToString(auditLog)));
        console.log("");
        console.log("========================================");
        console.log("  COPY THIS INTO backend/.env");
        console.log("========================================");
        console.log("");
        console.log("# TrustChain Contract Addresses");
        console.log(string.concat("TRUST_SCORE_ADDRESS=",    _addrToString(trustScore)));
        console.log(string.concat("IDENTITY_ADDRESS=",       _addrToString(identityRegistry)));
        console.log(string.concat("AUDIT_LOG_ADDRESS=",      _addrToString(auditLog)));
        console.log(string.concat("DEPLOYER_ADDRESS=",       _addrToString(deployer)));
        console.log("");
        console.log("# Monad Testnet");
        console.log("MONAD_RPC_URL=https://testnet-rpc.monad.xyz");
        console.log("CHAIN_ID=10143");
        console.log("");
        console.log("========================================");
        console.log("  NEXT STEPS");
        console.log("========================================");
        console.log("1. Copy the .env block above into backend/.env");
        console.log("2. Run: python3 scripts/compute_hashes.py");
        console.log("3. Set the 4 *_HASH values in .env from its output");
        console.log("4. Re-run this script with real hashes to update registration");
        console.log("   OR call identityRegistry.registerAgent() directly via Python");
        console.log("5. Copy ABIs:");
        console.log("   cp out/AgentAuditLog.sol/AgentAuditLog.json backend/contracts/");
        console.log("   cp out/TrustScoreRegistry.sol/TrustScoreRegistry.json backend/contracts/");
        console.log("   cp out/AgentIdentityRegistry.sol/AgentIdentityRegistry.json backend/contracts/");
        console.log("");
    }

    /// @dev Convert address to checksummed hex string for console output
    function _addrToString(address addr) internal pure returns (string memory) {
        bytes memory data    = abi.encodePacked(addr);
        bytes memory hexChars = "0123456789abcdef";
        bytes memory str      = new bytes(42);
        str[0] = "0";
        str[1] = "x";
        for (uint256 i = 0; i < 20; i++) {
            str[2 + i * 2]     = hexChars[uint8(data[i] >> 4)];
            str[3 + i * 2]     = hexChars[uint8(data[i] & 0x0f)];
        }
        return string(str);
    }
}
