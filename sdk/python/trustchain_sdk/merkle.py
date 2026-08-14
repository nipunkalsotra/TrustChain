"""
trustchain_sdk.merkle — local Merkle inclusion-proof verification.

Mirrors backend/blockchain/merkle.py's `hash_pair`/`verify_proof` EXACTLY
(sorted-pair keccak256, matching OpenZeppelin's MerkleProof.sol) so a
proof returned by GET /steps/{id}/proof verifies identically here as it
does on-chain — the whole point of shipping proof verification in the
SDK at all is that a caller doesn't have to trust TrustChain's own API
response, only the math and the on-chain root it's checked against.

`verify_proof` is a pure, local, no-network computation: fold the proof's
sibling hashes into the leaf and compare to the claimed root. That's
already meaningful (any tampering with `leaf` or an on-path sibling
breaks the fold), but it only proves internal consistency of the API
response — it does NOT independently confirm `root` is what's actually
anchored on-chain. For that stronger guarantee, see
TrustChain.verify_proof_onchain in instrumentation.py, which reads the
root from the real contract instead of trusting the value in the proof
object.
"""

from eth_utils import keccak as _keccak256


def hash_pair(a: bytes, b: bytes) -> bytes:
    """Sorted-pair hash — matches OpenZeppelin MerkleProof.sol's
    _hashPair (and backend/blockchain/merkle.py's hash_pair) exactly:
    always keccak256(min(a,b) || max(a,b)), regardless of which is
    "left" or "right"."""
    return _keccak256(a + b) if a < b else _keccak256(b + a)


def verify_proof(leaf: bytes, proof: list[bytes], root: bytes) -> bool:
    """Walk `proof` from `leaf`, folding in each sibling, and check the
    result matches `root`. Returns False (never raises) on a malformed
    or non-matching proof — verification failing is an expected,
    ordinary outcome to check for, not an exceptional one."""
    computed = leaf
    for sibling in proof:
        computed = hash_pair(computed, sibling)
    return computed == root


def hex_to_bytes(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("0x"))
