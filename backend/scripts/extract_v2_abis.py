"""
scripts/extract_v2_abis.py — pulls the `abi` field out of Foundry's compiled
artifacts (contracts/out/*.json, produced by `forge build`) into the small
{abi: [...]}-only files backend/contracts/*.json actually load at runtime
(blockchain/contracts_v2.py, anchor_worker/chain.py, indexer/chain.py).

Run from repo root after `forge build` in contracts/:
    python3 backend/scripts/extract_v2_abis.py

CI runs this in the backend job (see .github/workflows/test.yml) so the
committed contracts/src/v2/*.sol stays the single source of truth for
these ABIs — nothing here is hand-maintained or drifts silently out of
sync with the Solidity source.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_OUT = REPO_ROOT / "contracts" / "out"
BACKEND_CONTRACTS = REPO_ROOT / "backend" / "contracts"

CONTRACT_NAMES = ["AgentAuditLogV2", "TrustScoreRegistryV2", "AgentIdentityRegistryV2", "TrustChainRegistry"]


def main() -> None:
    BACKEND_CONTRACTS.mkdir(parents=True, exist_ok=True)
    for name in CONTRACT_NAMES:
        src = CONTRACTS_OUT / f"{name}.sol" / f"{name}.json"
        dst = BACKEND_CONTRACTS / f"{name}.json"
        with open(src) as f:
            data = json.load(f)
        with open(dst, "w") as f:
            json.dump({"abi": data["abi"]}, f, indent=2)
        print(f"{name}: {len(data['abi'])} ABI entries -> {dst}")


if __name__ == "__main__":
    main()
