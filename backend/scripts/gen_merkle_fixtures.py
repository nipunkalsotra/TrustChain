"""
scripts/gen_merkle_fixtures.py — regenerate the cross-language Merkle test
vectors consumed by contracts/test/MerkleCompat.t.sol.

Run this whenever blockchain/merkle.py's algorithm changes (leaf encoding,
pair-hashing, odd-node handling) — the Foundry test only proves the
*current* fixture file verifies on-chain; it can't catch an algorithm
change that wasn't re-exported here.

Deliberately a flat structure (n{N}_root, n{N}_leaves, n{N}_proof{i}) rather
than nested arrays-of-structs: Foundry's vm.parseJson + abi.decode is
fragile for nested/dynamic structures (JSON object keys get reordered
alphabetically when decoded into a struct, which silently produces wrong
results if you're not watching for it). Flat scalar/array keys sidestep
that entirely — every value here is read with a single
vm.parseJsonBytes32 / vm.parseJsonBytes32Array call.

Usage:
    cd backend
    python3 scripts/gen_merkle_fixtures.py
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blockchain.merkle import build_tree, leaf_hash  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent.parent / "contracts" / "test" / "fixtures" / "merkle_vectors.json"

TREE_SIZES = [1, 2, 3, 5, 8, 13]


def deterministic_leaf(rng: random.Random, i: int) -> bytes:
    return leaf_hash(
        run_id_hash=bytes(rng.randbytes(32)),
        agent_id_hash=bytes(rng.randbytes(32)),
        action_hash=bytes(rng.randbytes(32)),
        input_hash=bytes(rng.randbytes(32)),
        output_hash=bytes(rng.randbytes(32)),
        step_index=i,
        timestamp=1_700_000_000 + i,
    )


def hexlify(b: bytes) -> str:
    return "0x" + b.hex()


def main():
    rng = random.Random(1337)  # fixed seed — reproducible fixtures
    fixtures: dict = {}

    for n in TREE_SIZES:
        leaves = [deterministic_leaf(rng, i) for i in range(n)]
        tree = build_tree(leaves)

        fixtures[f"n{n}_root"] = tree.root_hex
        fixtures[f"n{n}_leaves"] = [hexlify(l) for l in leaves]
        for i in range(n):
            fixtures[f"n{n}_proof{i}"] = tree.proof_hex(i)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(fixtures, indent=2) + "\n")
    print(f"Wrote {len(TREE_SIZES)} trees ({sum(TREE_SIZES)} leaves total) to {OUT_PATH}")


if __name__ == "__main__":
    main()
