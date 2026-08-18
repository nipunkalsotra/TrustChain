/**
 * instrumentation.ts — the actual "any third-party agent can be audited
 * through a published SDK" surface, distinct from TrustChainClient (a
 * REST wrapper around TrustChain's OWN pipeline, POST /run-agent etc.).
 * Mirrors the Python SDK's trustchain_sdk/instrumentation.py — same
 * design, same principles, translated to JS idioms (see each method's
 * notes on where that translation isn't 1:1).
 *
 * Design principles this module is built around:
 *   - One line to adopt        -> new TrustChain(apiKey) + tc.log(...)
 *   - Never break the host app -> every call fails open (console.warn,
 *                                 never throws) unless onError="raise"
 *   - Non-blocking by default  -> log() is a SYNCHRONOUS function
 *                                 returning a StepReceipt immediately
 *                                 (stepId undefined — not known yet);
 *                                 the actual POST /steps call runs as a
 *                                 fire-and-forget promise that mutates
 *                                 the SAME receipt object once it
 *                                 resolves. logAndWait() is the awaited
 *                                 version for callers who need the real
 *                                 stepId right away (e.g. before
 *                                 getProof()).
 *   - Framework-native          -> see src/integrations/langchain.ts
 *   - Honest about state        -> a queued StepReceipt reports
 *                                 anchorStatus=undefined, not a guess
 */

import { randomUUID } from "node:crypto";

import { TrustChainClient, DEFAULT_BASE_URL } from "./client.js";
import { TrustChainError } from "./errors.js";
import { hexToBytes, keccak256Hex, verifyProof as verifyProofLocally } from "./merkle.js";

export interface StepReceipt {
  localId: string;
  status: string;
  stepId?: number;
  anchorStatus?: string;
  error?: string;
}

export interface VerifyResult {
  agentId: string;
  verified: boolean;
  isActive: boolean;
  hashMatches: boolean;
  storedHash: string;
  providedHash: string;
}

export interface MerkleProofResult {
  stepId: number;
  runId: string;
  leaf: string;
  proof: string[];
  root: string;
  txHash?: string;
  anchorStatus: string;
  anchorId?: number;
  /** Which leaf preimage produced `leaf` — 1 for every step anchored
   * before identity binding shipped, 2 for a step logged with an
   * agentCodeHash. See backend/blockchain/merkle.py's leaf_hash_v2. */
  leafSchemaVersion?: number;
  agentCodeHash?: string;
}

export type OnError = "warn" | "raise";

// Found by tests/conformance.test.ts (ADR-0017's stated open gap,
// closed here): Python's json.dumps defaults to ensure_ascii=True,
// escaping every non-ASCII character to \uXXXX — both backend/
// blockchain/hashing_utils.py::compute_hash AND the Python SDK's
// _code_hash rely on that default (neither passes ensure_ascii=False).
// JSON.stringify has no such mode; it leaves non-ASCII characters
// literal. Left alone, codeHash() and _code_hash() silently disagreed
// for ANY agentId/model/version/systemPrompt containing a non-ASCII
// character (accented letters, CJK, emoji, ...) — same hash-scheme
// class of risk the original comment below already warned about, just
// not the specific instance it anticipated. This replicates Python's
// escaping exactly: every UTF-16 code unit above U+007F becomes
// \uXXXX (lowercase hex, zero-padded to 4 digits) — JS strings are
// already UTF-16 internally, so this naturally produces the same
// surrogate-pair escaping Python's json module uses for characters
// outside the Basic Multilingual Plane, with no extra codepoint math
// needed.
function pythonEnsureAsciiEscape(jsonText: string): string {
  let out = "";
  for (let i = 0; i < jsonText.length; i++) {
    const code = jsonText.charCodeAt(i);
    out += code > 0x7f ? `\\u${code.toString(16).padStart(4, "0")}` : jsonText[i];
  }
  return out;
}

// Exported (not just used internally) specifically so
// tests/conformance.test.ts can call the SAME function the Python SDK's
// _code_hash is checked against, rather than a hand-reimplementation
// that could silently drift from what registerAgent()/log() actually
// send.
export function codeHash(agentId: string, model: string, version: string, systemPrompt: string): string {
  // MUST match backend/blockchain/hashing_utils.py's compute_hash (and
  // the Python SDK's _code_hash) exactly: same key names, same object,
  // same JSON serialisation (sorted keys, no whitespace, non-ASCII
  // escaped — see pythonEnsureAsciiEscape above) — or hashes computed
  // here will silently mismatch what's registered on-chain.
  const config: Record<string, string> = { agentId, model, version, systemPrompt };
  const sortedKeys = Object.keys(config).sort();
  const serialised = "{" + sortedKeys.map((k) => `${JSON.stringify(k)}:${JSON.stringify(config[k])}`).join(",") + "}";
  return keccak256Hex(pythonEnsureAsciiEscape(serialised));
}

export interface TrustChainOptions {
  baseUrl?: string;
  onError?: OnError;
}

export class TrustChain {
  private client: TrustChainClient;
  private onError: OnError;
  private pending: Set<Promise<void>> = new Set();
  private runIds: Map<string, string> = new Map();
  /** agentId -> codeHash, populated by registerAgent()/declareAgent().
   * log() attaches the cached hash to every step for that agentId — see
   * that method's docstring. Mirrors the Python SDK's
   * instrumentation.py::TrustChain._agent_hashes exactly. */
  private agentHashes: Map<string, string> = new Map();

  constructor(apiKey: string, options: TrustChainOptions = {}) {
    this.client = new TrustChainClient(apiKey, { baseUrl: options.baseUrl ?? DEFAULT_BASE_URL });
    this.onError = options.onError ?? "warn";
  }

  /** Blocks until every log() call queued so far has been sent (or
   * timeoutMs elapses). Call before process exit so buffered log() calls
   * aren't lost. Returns false on timeout (some steps may not be
   * durably queued server-side yet — not data loss, draining continues
   * in the background regardless). */
  async flush(timeoutMs = 10_000): Promise<boolean> {
    const settled = Promise.allSettled([...this.pending]).then(() => true);
    const timeout = new Promise<boolean>((resolve) => setTimeout(() => resolve(false), timeoutMs));
    return Promise.race([settled, timeout]);
  }

  async close(): Promise<void> {
    await this.flush();
  }

  private currentRunId(agentId: string): string {
    let runId = this.runIds.get(agentId);
    if (!runId) {
      runId = `sdk_${agentId}_${randomUUID().replace(/-/g, "")}`;
      this.runIds.set(agentId, runId);
    }
    return runId;
  }

  /** One run_id per agentId per TrustChain instance, generated once and
   * reused for every log() call on that agent. Call this to start a
   * fresh one (e.g. between distinct conversations the SDK can't infer
   * boundaries for on its own). */
  newRun(agentId: string): string {
    const runId = `sdk_${agentId}_${randomUUID().replace(/-/g, "")}`;
    this.runIds.set(agentId, runId);
    return runId;
  }

  // ── Registration / verification ──────────────────────────────────

  /** Caches codeHash for agentId on success — every subsequent log()/
   * audited() call for this agentId attaches it automatically (see
   * log()'s docstring). NOT cached on a failed call — a cached hash the
   * backend never actually saw registered would guarantee false-positive
   * drift alerts instead of the true "not registered, no check happens"
   * state. */
  async registerAgent(options: { agentId: string; model: string; version: string; systemPrompt: string }): Promise<string> {
    const hash = codeHash(options.agentId, options.model, options.version, options.systemPrompt);
    let response;
    try {
      response = await this.client.registerAgent(options.agentId, hash, options.model, options.version);
    } catch (e) {
      return this.handleError(e, "");
    }
    this.agentHashes.set(options.agentId, hash);
    return response.tx_hash;
  }

  /** Idempotent registration — registers ONLY if this exact
   * {agentId, model, version, systemPrompt} isn't already what's
   * cached, otherwise no-ops and returns "". Meant for a startup call
   * that runs on every boot without spamming AgentUpdated events (and
   * therefore agent_identity_changed alerts) on an unchanged config.
   * NOTE: checks the LOCAL cache only, not the server's registered hash
   * — call verifyAgent() first if you need to know whether the server's
   * state already matches. */
  async declareAgent(options: { agentId: string; model: string; version: string; systemPrompt: string }): Promise<string> {
    const hash = codeHash(options.agentId, options.model, options.version, options.systemPrompt);
    if (this.agentHashes.get(options.agentId) === hash) return "";
    return this.registerAgent(options);
  }

  async verifyAgent(options: { agentId: string; model: string; version: string; systemPrompt: string }): Promise<VerifyResult | undefined> {
    const hash = codeHash(options.agentId, options.model, options.version, options.systemPrompt);
    try {
      const response = (await this.client.verifyAgentOnchain(options.agentId, hash)) as any;
      return {
        agentId: options.agentId,
        verified: response.isValid,
        isActive: response.isActive,
        hashMatches: response.hashMatches,
        storedHash: response.storedHash,
        providedHash: response.providedHash,
      };
    } catch (e) {
      return this.handleError(e, undefined);
    }
  }

  // ── Logging ──────────────────────────────────────────────────────

  /** Non-blocking: returns a StepReceipt immediately (stepId undefined
   * — the server hasn't assigned one yet); the real POST /steps call
   * runs in the background and mutates this SAME object once it
   * resolves. Use logAndWait for the awaited version. */
  log(options: { agentId: string; action: string; input: string; output: string; trustScore?: number }): StepReceipt {
    const receipt: StepReceipt = { localId: randomUUID(), status: "queued" };
    const promise = this.sendLog(options, receipt);
    this.pending.add(promise);
    promise.finally(() => this.pending.delete(promise));
    return receipt;
  }

  async logAndWait(options: { agentId: string; action: string; input: string; output: string; trustScore?: number }): Promise<StepReceipt> {
    const receipt: StepReceipt = { localId: randomUUID(), status: "queued" };
    await this.sendLog(options, receipt);
    if (receipt.error !== undefined && this.onError === "raise") {
      throw new TrustChainError(receipt.error);
    }
    return receipt;
  }

  private async sendLog(
    options: { agentId: string; action: string; input: string; output: string; trustScore?: number },
    receipt: StepReceipt,
  ): Promise<void> {
    try {
      const response = await this.client.logStep({
        runId: this.currentRunId(options.agentId),
        agentId: options.agentId,
        action: options.action,
        input: options.input,
        output: options.output,
        trustScore: options.trustScore ?? 0,
        agentCodeHash: this.agentHashes.get(options.agentId),
      });
      receipt.stepId = response.step_id;
      receipt.status = response.status;
      receipt.anchorStatus = response.anchor_status;
    } catch (e) {
      receipt.error = e instanceof Error ? e.message : String(e);
      console.warn(`trustchain-sdk: failed to log step (agentId=${options.agentId}):`, e);
    }
  }

  /** Higher-order function form of Python's @tc.audited(...) decorator
   * — JS doesn't have a runtime-transparent equivalent for a plain
   * function, so this wraps one explicitly instead:
   *   const auditedFn = tc.audited("support-bot", "answer_query")(answerQuery);
   * Handles both a plain return value and a Promise-returning (async)
   * function — logs after the value/promise resolves either way. */
  audited<Args extends unknown[], R>(agentId: string, action: string) {
    return (fn: (...args: Args) => R) => {
      return (...args: Args): R => {
        const result = fn(...args);
        if (result && typeof (result as any).then === "function") {
          (result as unknown as Promise<unknown>).then(
            (resolved) => this.log({ agentId, action, input: safeJson(args), output: safeJson(resolved) }),
            (error) => this.log({ agentId, action: `${action}_error`, input: safeJson(args), output: safeJson(String(error)) }),
          );
        } else {
          this.log({ agentId, action, input: safeJson(args), output: safeJson(result) });
        }
        return result;
      };
    };
  }

  // ── Proofs ───────────────────────────────────────────────────────

  async getProof(stepId: number): Promise<MerkleProofResult | undefined> {
    try {
      const response = (await this.client.getStepProof(stepId)) as any;
      return {
        stepId: response.stepId, runId: response.runId, leaf: response.leaf, proof: response.proof,
        root: response.root, txHash: response.txHash, anchorStatus: response.anchorStatus,
        anchorId: response.anchorId, leafSchemaVersion: response.leafSchemaVersion ?? 1,
        agentCodeHash: response.agentCodeHash,
      };
    } catch (e) {
      return this.handleError(e, undefined);
    }
  }

  // ── Alerts ───────────────────────────────────────────────────────

  /** Read access to your org's alerts, so a team can pipe TrustChain
   * findings into their own on-call tooling. Needs an API key with the
   * alerts:read scope. */
  async alerts(options: { status?: string; severity?: string; limit?: number } = {}): Promise<unknown[]> {
    try {
      const response = await this.client.listAlerts(options);
      return response.alerts;
    } catch (e) {
      return this.handleError(e, []);
    }
  }

  /** LOCAL verification only — recomputes the root from leaf+proof and
   * compares to proof.root as returned by the API. Proves internal
   * consistency (tampering with the leaf or a sibling breaks the fold)
   * but does NOT independently confirm proof.root is what's actually
   * anchored on-chain — see verifyProofOnchain for that. */
  verifyProof(proof: MerkleProofResult): boolean {
    const leaf = hexToBytes(proof.leaf);
    const siblings = proof.proof.map(hexToBytes);
    const root = hexToBytes(proof.root);
    return verifyProofLocally(leaf, siblings, root);
  }

  /** Strongest form: reads AgentAuditLogV2.verifyProof(anchorId, leaf,
   * proof) directly from the chain at rpcUrl — the same call the
   * backend's own tests verify against. Requires knowing which chain/
   * contract to check (there's no safe way for the SDK to infer this),
   * so this is opt-in, not the default, and requires the optional
   * `viem` dependency (pip install trustchain-sdk[onchain]'s TS
   * equivalent: `npm install viem`). Returns false (never throws, even
   * on an RPC error) — a chain read failing is a normal "couldn't
   * confirm" outcome to handle, not a crash. */
  async verifyProofOnchain(proof: MerkleProofResult, rpcUrl: string, auditLogAddress: string): Promise<boolean> {
    if (proof.anchorId === undefined) return false;
    try {
      const { createPublicClient, http } = await import("viem");
      const client = createPublicClient({ transport: http(rpcUrl) });
      const abi = [{
        inputs: [
          { name: "anchorId", type: "uint256" },
          { name: "leaf", type: "bytes32" },
          { name: "proof", type: "bytes32[]" },
        ],
        name: "verifyProof",
        outputs: [{ name: "", type: "bool" }],
        stateMutability: "view",
        type: "function",
      }] as const;
      return await client.readContract({
        address: auditLogAddress as `0x${string}`,
        abi,
        functionName: "verifyProof",
        args: [BigInt(proof.anchorId), proof.leaf as `0x${string}`, proof.proof as `0x${string}`[]],
      });
    } catch (e) {
      console.warn("trustchain-sdk: on-chain proof verification failed:", e);
      return false;
    }
  }

  // ── Error handling ───────────────────────────────────────────────

  private handleError<T>(e: unknown, defaultValue: T): T {
    if (this.onError === "raise") {
      throw e instanceof TrustChainError ? e : new TrustChainError(String(e));
    }
    console.warn("trustchain-sdk:", e);
    return defaultValue;
  }
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
