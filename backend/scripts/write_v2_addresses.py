"""
scripts/write_v2_addresses.py — reads Foundry's broadcast receipt from the
most recent `forge script script/DeployV2.s.sol --broadcast` run and writes
backend/contracts/addresses_v2.json from it — the actual on-chain addresses
that run produced, not a hand-typed guess (CREATE addresses are
deterministic given the same deployer+nonce, but reading them back from the
broadcast log is still the only way to be sure they match what really
deployed).

Run from repo root, after deploying against a running Anvil:
    forge script script/DeployV2.s.sol --rpc-url http://localhost:8545 \\
        --private-key $ANVIL_KEY --broadcast
    python3 backend/scripts/write_v2_addresses.py --chain-id 31337

CI runs this in the backend job (see .github/workflows/test.yml) right
after that deploy step, so backend/contracts/addresses_v2.json always
reflects the CI run's own fresh Anvil deployment rather than a committed,
potentially-stale file.
"""

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_KEYS = ["AgentAuditLogV2", "TrustScoreRegistryV2", "AgentIdentityRegistryV2"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain-id", type=int, default=31337)
    args = parser.parse_args()

    broadcast_path = (
        REPO_ROOT / "contracts" / "broadcast" / "DeployV2.s.sol" / str(args.chain_id) / "run-latest.json"
    )
    with open(broadcast_path) as f:
        data = json.load(f)

    addresses = {
        tx["contractName"]: tx["contractAddress"]
        for tx in data["transactions"]
        if tx["transactionType"] == "CREATE" and tx["contractName"] in CONTRACT_KEYS
    }
    missing = set(CONTRACT_KEYS) - addresses.keys()
    if missing:
        raise RuntimeError(f"broadcast log at {broadcast_path} is missing CREATE txs for: {missing}")

    out = {"network": "anvil_local", "chainId": args.chain_id, **addresses}
    dst = REPO_ROOT / "backend" / "contracts" / "addresses_v2.json"
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"wrote {dst}:\n{json.dumps(out, indent=2)}")


if __name__ == "__main__":
    main()
