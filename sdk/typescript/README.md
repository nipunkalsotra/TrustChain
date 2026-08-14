# trustchain-sdk (TypeScript)

Two things live here, mirroring the Python SDK (`sdk/python`) exactly —
same design, same error taxonomy, both authenticating with an API key
(`tc_live_.../tc_test_...`, minted via `POST /api-keys`), no human login
required:

1. **`TrustChain`** — instrument YOUR OWN agent, running wherever it
   already runs. The actual "any third-party agent can be audited
   through a published SDK" surface.
2. **`TrustChainClient`** — a REST wrapper around TrustChain's *own*
   pipeline (`POST /run-agent`, SSE streaming, trust scores).

Core runtime dependency: `@noble/hashes` (keccak256 for local Merkle
verification). `viem` (on-chain proof verification) and `@langchain/core`
(the LangChain integration) are optional peer dependencies — only
install them if you use those specific features.

```bash
cd sdk/typescript
npm install
npm run build
```

## Instrumenting your own agent

```ts
import { TrustChain } from "trustchain-sdk";

const tc = new TrustChain("tc_live_...");

// Register the agent's fingerprint once (or whenever its config changes).
// systemPrompt is hashed CLIENT-SIDE here — the raw prompt is never sent.
await tc.registerAgent({
  agentId: "support-bot", model: "gpt-4o", version: "2026-01",
  systemPrompt: SUPPORT_PROMPT,
});

// Non-blocking by default: returns immediately with stepId undefined —
// the real POST /steps call runs in the background and fills it in.
const result = await myLlmCall(query);
tc.log({ agentId: "support-bot", action: "answer_query", input: query, output: result });

// Or synchronous, when you need the real stepId right away (e.g. before getProof):
const receipt = await tc.logAndWait({ agentId: "support-bot", action: "answer_query", input: query, output: result });

// Call before process exit so any still-queued log() calls aren't lost.
await tc.flush();
```

**Framework integration** — audits every LLM/tool call automatically
(needs `@langchain/core`: `npm install @langchain/core`):

```ts
import { TrustChainCallback } from "trustchain-sdk/dist/integrations/langchain.js";

const agent = createAgent(llm, tools, { callbacks: [new TrustChainCallback(tc, "support-bot")] });
```

**Verification:**

```ts
const result = await tc.verifyAgent({ agentId: "support-bot", model: "gpt-4o", version: "2026-01", systemPrompt: SUPPORT_PROMPT });
// { verified: true, isActive: true, hashMatches: true, ... }

const proof = await tc.getProof(stepId);          // MerkleProofResult
tc.verifyProof(proof);                             // boolean — local recompute, no network
await tc.verifyProofOnchain(proof, rpcUrl, auditLogAddress);  // boolean — reads the REAL contract (needs viem)
```

`verifyProof` is a pure local computation — meaningful, but it trusts
that `proof.root` is what TrustChain's API says it is. `verifyProofOnchain`
is the stronger form: it reads `AgentAuditLogV2.verifyProof(...)` directly
from the chain, so it doesn't have to trust the API's word for the root
at all — only the chain's.

### Failure handling

Every `TrustChain` method fails open by default (`onError: "warn"`, the
constructor's default) — an outage degrades to a `console.warn`, never a
thrown exception. Pass `onError: "raise"` to get real exceptions instead
(useful in tests/CI, where a silently-dropped step should fail loudly).

## Calling TrustChain's own pipeline

```ts
import { TrustChainClient } from "trustchain-sdk";

const client = new TrustChainClient("tc_live_...");

const finalEvent = await client.runAndWait("Research the top 3 AI startups in India");
console.log(finalEvent);

const scores = await client.trustScores(finalEvent!.runId as string);
console.log(scores);
```

Or drive the stream directly:

```ts
const started = await client.runAgent("...");
for await (const event of client.stream(started.run_id)) {
  console.log(event);
}
```

## CLI

```bash
npm run build
export TRUSTCHAIN_API_KEY=tc_live_...
node dist/cli.js run "Research the top 3 AI startups in India"
node dist/cli.js runs list --limit 10
node dist/cli.js runs get run_20260101_120000_abcd1234
node dist/cli.js scores run_20260101_120000_abcd1234
node dist/cli.js leaderboard
node dist/cli.js audit-log
```

`--api-key`/`--base-url` flags work too, and take priority over
`TRUSTCHAIN_API_KEY`/`TRUSTCHAIN_BASE_URL`. Run `node dist/cli.js help`
for the full command list. (Once published, this would install as the
`trustchain` binary via `npm install -g trustchain-sdk` — see
`package.json`'s `bin` field.)

## Error handling

Every method throws a `trustchain_sdk` error class on a non-2xx
response — never a raw `fetch`/`TypeError` for an API-level error. Same
taxonomy as the Python SDK: `BadRequestError` (400), `AuthenticationError`
(401), `AuthorizationError` (403), `NotFoundError` (404), `ConflictError`
(409), `ValidationError` (422), `RateLimitError` (429, has
`.retryAfterSeconds`), `ServerError` (5xx), `StreamTimeoutError` (client-side
stream timeout).

```ts
import { RateLimitError } from "trustchain-sdk";

try {
  await client.runAgent("...");
} catch (e) {
  if (e instanceof RateLimitError) {
    console.log(`back off for ${e.retryAfterSeconds}s`);
  }
}
```

## `stream()` consumes to the connection's natural end

Same reasoning as the Python SDK (see `sdk/python/README.md`'s note of
the same name): the server always sends one more synthetic
`run_complete` wrapper event after the pipeline's own last event, and
only sends it *after* the run's terminal status is committed to
Postgres. `stream()`/`runAndWait()` both wait for that, so `getRun()` is
guaranteed to succeed immediately after either returns — this client was
written that way from the start, having learned from the Python SDK
hitting (and fixing) the early-stop version of this bug first.

## Testing

`tests/client.test.ts` and `tests/instrumentation.test.ts` run against a
REAL TrustChain stack — no mocking of `fetch` or the API, using Node's
built-in test runner. The instrumentation tests additionally need real
Anvil with V2 deployed (`registerAgent`/`verifyAgent`/`getProof`/
`verifyProofOnchain` are all real on-chain reads/writes, verified
end-to-end including a real `AgentAuditLogV2.verifyProof()` call and its
negative case):

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
npm install
npm test
```

Automatically skipped if nothing answers at `http://localhost:8000`.

If `npm test` (which runs all files via `node --test tests/*.test.ts`)
comes back with every test unexpectedly SKIPPED, that's a known Node
test-runner quirk with multiple files + top-level `await` — not a real
failure. Run the affected file directly
(`node --import tsx --test tests/instrumentation.test.ts`) to confirm,
then just re-run `npm test`; it passes on retry.
