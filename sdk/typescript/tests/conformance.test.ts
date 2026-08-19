/**
 * tests/conformance.test.ts — cross-language hash conformance (Phase 3
 * ADR-0017's stated gap, closed here). No live backend needed — pure
 * function, both sides.
 *
 * `_code_hash()` (Python SDK, sdk/python/trustchain_sdk/instrumentation.py)
 * and `codeHash()` (this SDK, src/instrumentation.ts) MUST produce
 * byte-identical hashes from identical inputs, or every identity-drift
 * check (Phase 3 §6.2) silently mismatches for whichever SDK's language
 * a given agent happens to be instrumented in — the same risk the
 * pre-existing _code_hash docstring already calls out for registration,
 * now checked automatically instead of "by inspection."
 *
 * Expected values were computed independently with the Python SDK
 * (`python -c "from trustchain_sdk.instrumentation import _code_hash; ..."`)
 * against the exact same four inputs below — see this repo's Phase 3
 * session notes / ADR-0017 for how to regenerate them if either SDK's
 * hashing logic ever legitimately changes.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { codeHash } from "../src/instrumentation.js";

const VECTORS: Array<[string, string, string, string, string]> = [
  [
    "support-bot", "gpt-4o", "2025-11", "You are a support bot.",
    "0xcdd74cab93b54e845d42ea4b01c55ba77516c99794f75b9e6d6dab3a0d4dbde3",
  ],
  [
    "risk-scorer", "claude-sonnet-5", "3.1", "Score risk 1-100.",
    "0xadc606e61d285e5dbfba03d46628f903e0a5a8c773ee0f049f8c49dd8fd3f621",
  ],
  [
    "agent with spaces", "model/v1", "1.0.0", "unicode: héllo wörld 日本語",
    "0xe816928af1745a194a3a91709f65ec0620b1abc80d97d8cee591e61f1911a4a5",
  ],
];

test("codeHash matches the Python SDK's _code_hash for known vectors", () => {
  for (const [agentId, model, version, systemPrompt, expected] of VECTORS) {
    const actual = codeHash(agentId, model, version, systemPrompt);
    assert.equal(
      actual, expected,
      `codeHash(${agentId}) = ${actual}, expected ${expected} (Python SDK's _code_hash) — the two SDKs have diverged`,
    );
  }
});

test("codeHash is deterministic", () => {
  const a = codeHash("x", "m", "v", "p");
  const b = codeHash("x", "m", "v", "p");
  assert.equal(a, b);
});

test("codeHash changes if any single field changes", () => {
  const base = codeHash("agent", "model", "v1", "prompt");
  assert.notEqual(codeHash("agent2", "model", "v1", "prompt"), base);
  assert.notEqual(codeHash("agent", "model2", "v1", "prompt"), base);
  assert.notEqual(codeHash("agent", "model", "v2", "prompt"), base);
  assert.notEqual(codeHash("agent", "model", "v1", "prompt2"), base);
});
