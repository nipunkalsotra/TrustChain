# trustchain-sdk (TypeScript)

Client + CLI for the TrustChain API — start and stream agent pipeline
runs, read trust scores and audit history. Same design and error
taxonomy as the Python SDK (`sdk/python`), built for the same
"SDK-driven third-party agent" use case: authenticate with an API key
(`tc_live_.../tc_test_...`, minted via `POST /api-keys`), no human login
required. No runtime dependencies — uses the platform `fetch`.

```bash
cd sdk/typescript
npm install
npm run build
```

## Library

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

`tests/client.test.ts` runs against a REAL TrustChain stack — no mocking
of `fetch` or the API, using Node's built-in test runner:

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
npm install
npm test
```

Automatically skipped if nothing answers at `http://localhost:8000`.
