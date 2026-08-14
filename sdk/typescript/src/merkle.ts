/**
 * merkle.ts — local Merkle inclusion-proof verification.
 *
 * Mirrors backend/blockchain/merkle.py's `hash_pair`/`verify_proof`
 * EXACTLY (sorted-pair keccak256, matching OpenZeppelin's
 * MerkleProof.sol) — same reasoning as the Python SDK's
 * trustchain_sdk/merkle.py: a proof returned by GET /steps/{id}/proof
 * should verify identically here as it does on-chain, so a caller never
 * has to trust TrustChain's own API response for that, only the math.
 */

import { keccak_256 } from "@noble/hashes/sha3.js";

function keccak256(data: Uint8Array): Uint8Array {
  return keccak_256(data);
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}

/** Sorted-pair hash — matches OpenZeppelin MerkleProof.sol's _hashPair
 * (and both backend/blockchain/merkle.py and the Python SDK's copy)
 * exactly: always keccak256(min(a,b) || max(a,b)). */
export function hashPair(a: Uint8Array, b: Uint8Array): Uint8Array {
  return compareBytes(a, b) < 0 ? keccak256(concatBytes(a, b)) : keccak256(concatBytes(b, a));
}

/** Walk `proof` from `leaf`, folding in each sibling, and check the
 * result matches `root`. Returns false (never throws) on a malformed or
 * non-matching proof. */
export function verifyProof(leaf: Uint8Array, proof: Uint8Array[], root: Uint8Array): boolean {
  let computed = leaf;
  for (const sibling of proof) {
    computed = hashPair(computed, sibling);
  }
  return compareBytes(computed, root) === 0;
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

export function bytesToHex(bytes: Uint8Array): string {
  return "0x" + Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function keccak256Hex(text: string): string {
  return bytesToHex(keccak256(new TextEncoder().encode(text)));
}
