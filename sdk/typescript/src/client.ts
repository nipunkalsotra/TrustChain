/**
 * client.ts — TypeScript client for the TrustChain API.
 *
 * Built for the "SDK-driven third-party agent" use case main.py's own
 * POST /run-agent docstring calls out explicitly: an API key
 * (`tc_live_.../tc_test_...`, see POST /api-keys) authenticates without
 * any human login, in the same `Authorization: Bearer <key>` header a
 * session JWT would use — see backend/auth.py's get_current_principal.
 *
 * Uses the platform `fetch` (Node 18+, no runtime dependencies) for both
 * plain requests and the SSE stream (read manually via the response
 * body's ReadableStream — Node has no built-in browser-style
 * EventSource).
 */

import {
  AuthenticationError,
  AuthorizationError,
  BadRequestError,
  ConflictError,
  NotFoundError,
  RateLimitError,
  ServerError,
  StreamTimeoutError,
  TrustChainError,
  ValidationError,
} from "./errors.js";

export const DEFAULT_BASE_URL = "http://localhost:8000";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const DEFAULT_STREAM_TIMEOUT_MS = 120_000;

export interface TrustChainClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
}

export interface RunAgentResponse {
  run_id: string;
  task: string;
  status: string;
  stream_url: string;
}

export interface SseEvent {
  type?: string;
  runId?: string;
  [key: string]: unknown;
}

async function raiseForStatus(response: Response): Promise<void> {
  if (response.ok) return;

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const detail = body && typeof body === "object" && "detail" in body ? (body as any).detail : body;
  // error_code (backend/errors.py) is undefined for responses that
  // predate the typed error taxonomy, or a validation-error body (422's
  // `detail` is FastAPI's own list-of-errors shape, not this API's
  // ApiError at all — Pydantic validation never goes through ApiError).
  const errorCode =
    body && typeof body === "object" && "error_code" in body ? (body as any).error_code : undefined;
  const status = response.status;
  const message = `TrustChain API returned ${status}: ${JSON.stringify(detail)}`;

  switch (status) {
    case 400:
      throw new BadRequestError(message, status, detail, errorCode);
    case 401:
      throw new AuthenticationError(message, status, detail, errorCode);
    case 403:
      throw new AuthorizationError(message, status, detail, errorCode);
    case 404:
      throw new NotFoundError(message, status, detail, errorCode);
    case 409:
      throw new ConflictError(message, status, detail, errorCode);
    case 422:
      throw new ValidationError(message, status, detail, errorCode);
    case 429: {
      const retryAfter = response.headers.get("Retry-After");
      throw new RateLimitError(
        message, status, detail, retryAfter !== null ? Number(retryAfter) : undefined, errorCode,
      );
    }
    default:
      if (status >= 500) throw new ServerError(message, status, detail, errorCode);
      throw new TrustChainError(message, status, detail, errorCode);
  }
}

function parseSseLine(line: string): SseEvent | null {
  if (!line.startsWith("data: ")) return null;
  return JSON.parse(line.slice("data: ".length)) as SseEvent;
}

export class TrustChainClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;

  constructor(apiKey: string, options: TrustChainClientOptions = {}) {
    if (!apiKey) {
      throw new Error("apiKey is required — create one via POST /api-keys (see the README)");
    }
    this.apiKey = apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  private async request<T>(
    method: string,
    path: string,
    options: { json?: unknown; params?: Record<string, string | number | undefined>; headers?: Record<string, string> } = {},
  ): Promise<T> {
    const url = new URL(this.baseUrl + path);
    if (options.params) {
      for (const [key, value] of Object.entries(options.params)) {
        if (value !== undefined) url.searchParams.set(key, String(value));
      }
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(url, {
        method,
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
          ...options.headers,
        },
        body: options.json !== undefined ? JSON.stringify(options.json) : undefined,
        signal: controller.signal,
      });
      await raiseForStatus(response);
      return (await response.json()) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  async runAgent(
    task: string,
    options: { runId?: string; idempotencyKey?: string } = {},
  ): Promise<RunAgentResponse> {
    return this.request<RunAgentResponse>("POST", "/run-agent", {
      json: { task, ...(options.runId ? { run_id: options.runId } : {}) },
      headers: options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {},
    });
  }

  async getRun(runId: string): Promise<unknown> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}`);
  }

  async listRuns(limit = 50): Promise<{ runs: unknown[]; total: number }> {
    return this.request("GET", "/runs", { params: { limit } });
  }

  async trustScores(runId: string): Promise<{ runId: string; scores: unknown }> {
    return this.request("GET", "/trust-scores", { params: { run_id: runId } });
  }

  async trustScoreHistory(runId: string): Promise<{ runId: string; history: unknown }> {
    return this.request("GET", "/trust-scores/history", { params: { run_id: runId } });
  }

  async leaderboard(maxRuns = 50): Promise<unknown> {
    return this.request("GET", "/leaderboard", { params: { max_runs: maxRuns } });
  }

  async auditLog(runId?: string): Promise<{ entries: unknown[]; total: number }> {
    return this.request("GET", "/audit-log", { params: runId ? { run_id: runId } : {} });
  }

  /** Low-level — takes an already-computed codeHash. Most callers want
   * the instrumentation TrustChain class's registerAgent (which hashes
   * {agentId, model, version, systemPrompt} client-side first, never
   * sending the raw prompt) rather than this directly. */
  async registerAgent(agentId: string, codeHash: string, model: string, version: string): Promise<{ agent_id: string; tx_hash: string }> {
    return this.request("POST", "/agents", {
      json: { agent_id: agentId, code_hash: codeHash, model, version },
    });
  }

  async verifyAgentOnchain(agentId: string, codeHash: string): Promise<Record<string, unknown>> {
    return this.request("GET", `/agents/${encodeURIComponent(agentId)}/verify`, { params: { code_hash: codeHash } });
  }

  /** Low-level, synchronous SDK-ingest call (POST /steps) — most callers
   * want the instrumentation TrustChain class's log() (non-blocking by
   * default) rather than this directly. */
  async logStep(options: {
    runId: string; agentId: string; action: string; input: string; output: string;
    trustScore?: number; idempotencyKey?: string;
  }): Promise<{ step_id: number; outbox_id: number; status: string; anchor_status: string }> {
    return this.request("POST", "/steps", {
      json: {
        run_id: options.runId, agent_id: options.agentId, action: options.action,
        input: options.input, output: options.output, trust_score: options.trustScore ?? 0,
      },
      headers: options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {},
    });
  }

  async getStepProof(stepId: number): Promise<Record<string, unknown>> {
    return this.request("GET", `/steps/${stepId}/proof`);
  }

  /** GET /stats — public, no auth required (this call still sends the
   * configured API key like every other method here; the endpoint
   * simply ignores it). */
  async platformStats(): Promise<{ totalRuns: number; totalSteps: number; totalAnchoredBatches: number }> {
    return this.request("GET", "/stats");
  }

  /**
   * Yields parsed SSE events for a run as they arrive. Deliberately
   * unauthenticated on the server side (see main.py's stream_events
   * docstring — browser EventSource can't send an Authorization
   * header), so this doesn't send the API key either; it only needs
   * the run_id, same as the browser frontend.
   *
   * Consumes the stream to its NATURAL end (connection close) rather
   * than returning as soon as it sees a `type: "run_complete"` or
   * `type: "error"` event. main.py's stream_events always sends ONE
   * MORE synthetic `run_complete` wrapper event after the pipeline's own
   * last event, and critically, that wrapper is only sent AFTER the
   * run's terminal status has already been committed to Postgres
   * (db.complete_run/fail_run — see main.py's _run_pipeline_background:
   * both are awaited before run_events.publish_terminal(), which is what
   * makes read_events() return, which is what makes this wrapper get
   * sent). Stopping early on the pipeline's own "error"/"run_complete"
   * event races that DB write: a caller who saw "error" and immediately
   * called getRun() could get a 404 ("not yet complete") even though the
   * stream had already said the run was done — this exact bug was found
   * and fixed in the Python SDK first (sdk/python/trustchain_sdk/
   * client.py); this client is written the same way from the start.
   *
   * Raises StreamTimeoutError if the stream goes quiet for longer than
   * `timeoutMs` without the connection closing.
   */
  async *stream(runId: string, timeoutMs = DEFAULT_STREAM_TIMEOUT_MS): AsyncGenerator<SseEvent, void, void> {
    const controller = new AbortController();
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs);
    };

    resetIdleTimer();
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/stream/${encodeURIComponent(runId)}`, { signal: controller.signal });
    } catch (e) {
      if (idleTimer) clearTimeout(idleTimer);
      if (timedOut) throw new StreamTimeoutError(`stream for run ${runId} timed out after ${timeoutMs}ms with no terminal event`);
      throw e;
    }

    try {
      await raiseForStatus(response);
      if (!response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          resetIdleTimer();
          let chunk;
          try {
            chunk = await reader.read();
          } catch (e) {
            if (timedOut) {
              throw new StreamTimeoutError(`stream for run ${runId} timed out after ${timeoutMs}ms with no terminal event`);
            }
            throw e;
          }
          if (chunk.done) break;

          buffer += decoder.decode(chunk.value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const event = parseSseLine(line);
            if (event !== null) yield event;
          }
        }
      } finally {
        if (idleTimer) clearTimeout(idleTimer);
        reader.releaseLock();
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
    }
  }

  /** Convenience wrapper: start a run and block until the stream reaches
   * its natural end, returning the LAST event seen. Most agent
   * integrations want this rather than manually wiring
   * runAgent()+stream(). */
  async runAndWait(task: string, timeoutMs = DEFAULT_STREAM_TIMEOUT_MS): Promise<SseEvent | undefined> {
    const started = await this.runAgent(task);
    let finalEvent: SseEvent | undefined;
    for await (const event of this.stream(started.run_id, timeoutMs)) {
      finalEvent = event;
    }
    return finalEvent;
  }
}
