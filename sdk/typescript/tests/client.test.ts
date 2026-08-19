/**
 * tests/client.test.ts — integration tests for TrustChainClient against a
 * REAL running API (docker compose up postgres redis anvil api
 * anchor-worker indexer mcp-search mcp-blockchain, with V2 contracts
 * deployed) — no mocking of fetch or the API. Skipped automatically if
 * nothing answers at BASE_URL.
 *
 * Run:
 *   docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
 *   npm install && npm test
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import {
  AuthenticationError,
  BadRequestError,
  NotFoundError,
  RateLimitError,
  TrustChainClient,
  ValidationError,
} from "../src/index.js";
import { verifiedSignup } from "./testHelpers.js";

const BASE_URL = "http://localhost:8000";

async function stackIsUp(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function freshApiKey(): Promise<string> {
  // `assert.equal(res.status, 200, await res.text())` would read the
  // body EAGERLY as the assertion's diagnostic message argument — even
  // on the success path, since JS evaluates all call arguments before
  // the call happens — leaving nothing for a later `.json()` call to
  // read ("Body is unusable: Body has already been read"). Read the body
  // exactly once instead, and only construct a failure message from it
  // when actually failing.
  const email = `sdk_ts_test_${randomUUID()}@example.com`;
  const token = await verifiedSignup(BASE_URL, "TS SDK integration test", email, "sdk-ts-test-password-123");

  const createdRes = await fetch(`${BASE_URL}/api-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ scopes: ["runs:write", "runs:read"], environment: "test" }),
  });
  const createdBody = await createdRes.text();
  assert.equal(createdRes.status, 200, createdBody);
  const { raw_key } = JSON.parse(createdBody) as { raw_key: string };
  return raw_key;
}

// A fresh API key per test function (not shared) — every project gets
// its own POST /run-agent rate-limit bucket (backend/rate_limit.py,
// default capacity 5/min); sharing one across several run_agent()-calling
// tests would make them spuriously rate-limit each other depending on
// execution order (exactly the bug the Python SDK's test suite hit
// first with a module-scoped fixture — see sdk/python/tests/test_client.py).

const up = await stackIsUp();

test("runAgent returns run_id and stream_url", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const result = await client.runAgent(`ts sdk integration test task ${randomUUID()}`);
  assert.equal(result.status, "started");
  assert.ok(result.run_id);
  assert.equal(result.stream_url, `/stream/${result.run_id}`);
});

test("getRun for an unknown run_id raises NotFoundError", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  await assert.rejects(() => client.getRun("run_this_definitely_does_not_exist_00000000"), NotFoundError);
});

test("listRuns includes a run just created", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const started = await client.runAgent(`ts sdk integration test task ${randomUUID()}`);
  const listing = (await client.listRuns(50)) as { runs: Array<{ runId: string }> };
  assert.ok(listing.runs.some((r) => r.runId === started.run_id));
});

test("stream yields events ending in the synthetic run_complete wrapper", { skip: !up }, async () => {
  // The stream's actual last event is a synthetic "run_complete" WRAPPER
  // main.py's stream_events always appends after the pipeline's own
  // events, sent only once the run's terminal status is already
  // committed to Postgres — true whether the pipeline itself succeeded
  // or errored. See client.ts's stream() docstring.
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const started = await client.runAgent(`ts sdk integration test task ${randomUUID()}`);
  const events = [];
  for await (const event of client.stream(started.run_id, 90_000)) {
    events.push(event);
  }
  assert.ok(events.length > 0, "expected at least one SSE event");
  assert.equal(events[events.length - 1].type, "run_complete");
  for (const e of events) {
    if (e.runId !== undefined) assert.equal(e.runId, started.run_id);
  }
});

test("runAndWait blocks until the terminal event", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const finalEvent = await client.runAndWait(`ts sdk integration test task ${randomUUID()}`, 90_000);
  assert.equal(finalEvent?.type, "run_complete");
});

test("getRun is immediately queryable right after stream drains (no race)", { skip: !up }, async () => {
  // Regression coverage matching the Python SDK's test of the same name:
  // stream() must consume to the stream's natural end (not stop early on
  // the pipeline's own error/run_complete event) so that by the time it
  // returns, GET /runs/{run_id} is guaranteed queryable. No retry here on
  // purpose — if this flakes, the race is back.
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const started = await client.runAgent(`ts sdk integration test task ${randomUUID()}`);
  for await (const _event of client.stream(started.run_id, 90_000)) {
    // drain
  }
  await client.getRun(started.run_id); // must not throw NotFoundError
});

test("leaderboard and audit-log return real data shapes", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  const leaderboard = (await client.leaderboard()) as Record<string, unknown>;
  assert.deepEqual(new Set(Object.keys(leaderboard)), new Set(["agents", "totalRuns", "runsConsidered"]));

  const audit = await client.auditLog();
  assert.deepEqual(new Set(Object.keys(audit)), new Set(["entries", "total"]));
  assert.equal(audit.total, audit.entries.length);
});

test("invalid API key raises AuthenticationError", { skip: !up }, async () => {
  const client = new TrustChainClient("tc_test_not_a_real_key_00000000000000000000", { baseUrl: BASE_URL });
  await assert.rejects(() => client.listRuns(), AuthenticationError);
});

test("invalid API key's error carries the typed errorCode", { skip: !up }, async () => {
  // error_code (backend/errors.py's typed taxonomy) must reach the SDK
  // error object, not just the raw response body — this is what a
  // caller actually branches on instead of parsing .detail strings.
  const client = new TrustChainClient("tc_test_not_a_real_key_00000000000000000000", { baseUrl: BASE_URL });
  try {
    await client.listRuns();
    assert.fail("expected listRuns() to reject");
  } catch (e) {
    assert.ok(e instanceof AuthenticationError);
    assert.equal((e as AuthenticationError).errorCode, "invalid_api_key");
  }
});

test("empty task raises ValidationError", { skip: !up }, async () => {
  // RunAgentRequest.task has Field(min_length=1) — an empty string is
  // rejected at the Pydantic schema layer (422) before main.py's
  // handler body (and its own `if not body.task.strip()` check, still
  // real but now only reachable for a WHITESPACE-only task) ever runs.
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  await assert.rejects(() => client.runAgent(""), ValidationError);
});

test("whitespace-only task raises BadRequestError", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  await assert.rejects(() => client.runAgent("   "), BadRequestError);
});

test("whitespace-only task's error carries the typed errorCode", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  try {
    await client.runAgent("   ");
    assert.fail("expected runAgent() to reject");
  } catch (e) {
    assert.ok(e instanceof BadRequestError);
    assert.equal((e as BadRequestError).errorCode, "task_empty");
  }
});

test("bursting past the rate limit raises RateLimitError with retryAfterSeconds", { skip: !up }, async () => {
  const client = new TrustChainClient(await freshApiKey(), { baseUrl: BASE_URL });
  let sawRateLimitError = false;
  for (let i = 0; i < 20; i++) {
    try {
      await client.runAgent(`ts sdk integration test burst ${randomUUID()}`);
    } catch (e) {
      if (e instanceof RateLimitError) {
        sawRateLimitError = true;
        assert.ok(e.retryAfterSeconds !== undefined);
        break;
      }
      throw e;
    }
  }
  assert.ok(sawRateLimitError, "expected at least one RateLimitError from a 20-call burst");
});
