/**
 * tests/instrumentation.test.ts — integration tests for the TrustChain
 * instrumentation class, against a REAL running API + real Anvil with V2
 * deployed — no mocking.
 *
 * Run:
 *   docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
 *   npm install
 *   npm test
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { TrustChain, hashPair, verifyProof } from "../src/index.js";
import { verifiedSignup } from "./testHelpers.js";

const BASE_URL = "http://localhost:8000";
const ANVIL_RPC = "http://localhost:8545";

async function stackIsUp(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function anvilIsUp(): Promise<boolean> {
  try {
    const res = await fetch(ANVIL_RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", method: "eth_chainId", params: [], id: 1 }),
      signal: AbortSignal.timeout(2000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function freshApiKey(): Promise<string> {
  const email = `sdk_ts_instr_${randomUUID()}@example.com`;
  const token = await verifiedSignup(BASE_URL, "TS instrumentation test", email, "sdk-ts-instr-password-123");

  const createdRes = await fetch(`${BASE_URL}/api-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      scopes: ["runs:write", "runs:read", "logs:write", "agents:register", "agents:read"],
      environment: "test",
    }),
  });
  const createdBody = await createdRes.text();
  assert.equal(createdRes.status, 200, createdBody);
  const { raw_key } = JSON.parse(createdBody) as { raw_key: string };
  return raw_key;
}

function getV2Addresses(): Record<string, string> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(here, "..", "..", "..");
  const raw = readFileSync(path.join(repoRoot, "backend", "contracts", "addresses_v2.json"), "utf-8");
  return JSON.parse(raw);
}

function hexToBytesLocal(hex: string): Uint8Array {
  const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return out;
}

const up = await stackIsUp();
const anvilUp = up && (await anvilIsUp());

// ── merkle.ts — pure local, no network ───────────────────────────────────

test("hashPair is order-independent and produces a 32-byte digest", () => {
  const a = new Uint8Array(Array.from({ length: 32 }, (_, i) => i));
  const b = new Uint8Array(Array.from({ length: 32 }, (_, i) => i + 32));
  const ab = hashPair(a, b);
  const ba = hashPair(b, a);
  assert.deepEqual(ab, ba);
  assert.equal(ab.length, 32);
});

test("verifyProof accepts a valid proof and rejects a tampered leaf", () => {
  const leaf0 = new Uint8Array(Array.from({ length: 32 }, (_, i) => i + 1));
  const leaf1 = new Uint8Array(Array.from({ length: 32 }, (_, i) => i + 33));
  const root = hashPair(leaf0, leaf1);

  assert.equal(verifyProof(leaf0, [leaf1], root), true);
  assert.equal(verifyProof(leaf1, [leaf0], root), true);
  assert.equal(verifyProof(new Uint8Array(32), [leaf1], root), false);
});

// ── register/verify agent — real on-chain ────────────────────────────────

test("registerAgent then verifyAgent matches", { skip: !anvilUp }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const agentId = `ts_sdk_agent_${randomUUID().slice(0, 8)}`;

  const txHash = await tc.registerAgent({ agentId, model: "gpt-4o", version: "2026-01", systemPrompt: "You are helpful." });
  assert.ok(txHash.startsWith("0x"));

  const result = await tc.verifyAgent({ agentId, model: "gpt-4o", version: "2026-01", systemPrompt: "You are helpful." });
  assert.ok(result);
  assert.equal(result!.verified, true);
  assert.equal(result!.isActive, true);
  assert.equal(result!.hashMatches, true);
});

test("verifyAgent detects a tampered prompt", { skip: !anvilUp }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const agentId = `ts_sdk_agent_${randomUUID().slice(0, 8)}`;
  await tc.registerAgent({ agentId, model: "gpt-4o", version: "2026-01", systemPrompt: "Original." });

  const result = await tc.verifyAgent({ agentId, model: "gpt-4o", version: "2026-01", systemPrompt: "TAMPERED." });
  assert.equal(result!.verified, false);
  assert.equal(result!.hashMatches, false);
});

test("registerAgent fails open by default with bad credentials", { skip: !up }, async () => {
  const tc = new TrustChain("tc_test_not_a_real_key_00000000000000000000", { baseUrl: BASE_URL });
  const txHash = await tc.registerAgent({ agentId: "x", model: "m", version: "v", systemPrompt: "p" });
  assert.equal(txHash, "");
});

test("registerAgent throws with onError raise and bad credentials", { skip: !up }, async () => {
  const tc = new TrustChain("tc_test_not_a_real_key_00000000000000000000", { baseUrl: BASE_URL, onError: "raise" });
  await assert.rejects(() => tc.registerAgent({ agentId: "x", model: "m", version: "v", systemPrompt: "p" }));
});

// ── log()/logAndWait() ────────────────────────────────────────────────────

test("logAndWait returns the real stepId", { skip: !up }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const receipt = await tc.logAndWait({ agentId: "test-agent", action: "answer", input: "q", output: "a" });
  assert.equal(receipt.error, undefined);
  assert.notEqual(receipt.stepId, undefined);
  assert.equal(receipt.anchorStatus, "pending");
});

test("log is non-blocking and eventually completes", { skip: !up }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const receipt = tc.log({ agentId: "test-agent", action: "answer", input: "q", output: "a" });
  // Non-blocking: stepId isn't known yet the moment log() returns.
  assert.equal(receipt.stepId, undefined);
  assert.equal(receipt.status, "queued");

  const flushed = await tc.flush(10_000);
  assert.equal(flushed, true);
  assert.notEqual(receipt.stepId, undefined);
  assert.equal(receipt.error, undefined);
});

test("audited() wraps a sync function and logs a step", { skip: !up }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const compute = (x: number): number => x * 2;
  const auditedCompute = tc.audited<[number], number>("decorated-agent", "compute")(compute);

  assert.equal(auditedCompute(21), 42);
  assert.equal(await tc.flush(10_000), true);
});

test("newRun starts a fresh run id", { skip: !up }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
  const receipt = await tc.logAndWait({ agentId: "run-test-agent", action: "a", input: "i", output: "o" });
  assert.notEqual(receipt.stepId, undefined);

  const firstRunId = (tc as any).currentRunId("run-test-agent");
  tc.newRun("run-test-agent");
  const secondRunId = (tc as any).currentRunId("run-test-agent");
  assert.notEqual(firstRunId, secondRunId);
});

// ── get/verify proof ───────────────────────────────────────────────────────

test("getProof for a not-yet-anchored step fails open", { skip: !anvilUp }, async () => {
  const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL });
  const receipt = await tc.logAndWait({ agentId: "proof-test-agent", action: "a", input: "i", output: "o" });
  assert.notEqual(receipt.stepId, undefined);

  const proof = await tc.getProof(receipt.stepId!);
  assert.equal(proof, undefined);
});

test(
  "getProof after real anchoring verifies locally and on-chain",
  { skip: !anvilUp },
  async () => {
    const tc = new TrustChain(await freshApiKey(), { baseUrl: BASE_URL, onError: "raise" });
    const receipt = await tc.logAndWait({ agentId: "proof-test-agent", action: "a", input: "i", output: "o" });
    assert.notEqual(receipt.stepId, undefined);

    // Polls until anchorStatus === "confirmed" specifically, not just
    // until getProof stops returning undefined — see the Python SDK's
    // equivalent test (sdk/python/tests/test_instrumentation.py::
    // test_get_proof_after_real_anchoring_verifies_locally_and_onchain)
    // for the full reasoning: GET /steps/{id}/proof returns a real proof
    // the moment the anchor-worker places a step into a batch, while
    // that batch is still "building" — a loop exiting as soon as a proof
    // exists at all races the batch's later "confirmed" transition
    // rather than actually waiting for it, a real bug (not flaky luck)
    // that surfaced as an intermittent CI failure once a slower CI
    // runner made the race actually visible.
    let proof;
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      let candidate;
      try {
        candidate = await tc.getProof(receipt.stepId!);
      } catch {
        candidate = undefined;
      }
      if (candidate && candidate.anchorStatus === "confirmed") {
        proof = candidate;
        break;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }

    assert.ok(proof, "step was not anchored to 'confirmed' by the anchor-worker container within 60s");
    assert.equal(proof!.anchorStatus, "confirmed");
    assert.notEqual(proof!.anchorId, undefined);

    assert.equal(tc.verifyProof(proof!), true);

    const addresses = getV2Addresses();
    const onchainOk = await tc.verifyProofOnchain(proof!, ANVIL_RPC, addresses.AgentAuditLogV2);
    assert.equal(onchainOk, true);

    const forged = { ...proof!, leaf: "0x" + "00".repeat(32) };
    assert.equal(tc.verifyProof(forged), false);
    const forgedOnchain = await tc.verifyProofOnchain(forged, ANVIL_RPC, addresses.AgentAuditLogV2);
    assert.equal(forgedOnchain, false);
  },
);
