# trustchain-sdk (Python)

Client for the TrustChain API — start and stream agent pipeline runs, read
trust scores and audit history. Built for the "SDK-driven third-party
agent" use case: authenticate with an API key (`tc_live_.../tc_test_...`,
minted via `POST /api-keys`), no human login required.

```bash
pip install -e sdk/python   # from the repo root, until this is published
```

## Quickstart

```python
from trustchain_sdk import TrustChainClient

with TrustChainClient(api_key="tc_live_...") as client:
    final_event = client.run_and_wait("Research the top 3 AI startups in India")
    print(final_event)

    scores = client.trust_scores(final_event["runId"])
    print(scores)
```

Async, for use inside an async agent framework (this backend's own
pipeline is async end-to-end via LangGraph):

```python
from trustchain_sdk import AsyncTrustChainClient

async with AsyncTrustChainClient(api_key="tc_live_...") as client:
    started = await client.run_agent("Research the top 3 AI startups in India")
    async for event in client.stream(started["run_id"]):
        print(event)
```

## Getting an API key

An API key is scoped to a project and minted by a human, authenticated
user (`POST /api-keys`, requires the owner/admin role — see
`docs/multisig-admin-handoff.md`'s sibling docs for how project access is
structured):

```python
import httpx

token = httpx.post("http://localhost:8000/auth/signup", json={
    "name": "...", "email": "...", "password": "...",
}).json()["token"]

key = httpx.post(
    "http://localhost:8000/api-keys",
    json={"scopes": ["runs:write", "runs:read"], "environment": "live"},
    headers={"Authorization": f"Bearer {token}"},
).json()["raw_key"]  # shown exactly once — store it now
```

## Error handling

Every method raises a `trustchain_sdk.exceptions.TrustChainError`
subclass on a non-2xx response — never a raw `httpx` exception for an
API-level error:

| Exception | Status | When |
|---|---|---|
| `BadRequestError` | 400 | An application-level input check failed (e.g. an empty task) |
| `AuthenticationError` | 401 | Missing/invalid API key |
| `AuthorizationError` | 403 | Valid key, missing scope |
| `NotFoundError` | 404 | Doesn't exist, isn't yours, or (for a run) isn't finished yet |
| `ConflictError` | 409 | `Idempotency-Key` reused with a different request body |
| `ValidationError` | 422 | Request failed schema validation |
| `RateLimitError` | 429 | Rate limit or monthly quota exceeded — `.retry_after_seconds` is set |
| `ServerError` | 5xx | The API's own fault |
| `StreamTimeoutError` | — | `stream()` went quiet longer than its `timeout` |

```python
from trustchain_sdk import RateLimitError

try:
    client.run_agent("...")
except RateLimitError as e:
    print(f"back off for {e.retry_after_seconds}s")
```

## Idempotent run submission

```python
client.run_agent("...", idempotency_key="my-retry-key-123")
```

Retrying with the same key and the same task returns the original
response instead of starting a second run. Retrying with the same key
but a *different* task raises `ConflictError`.

## A note on `stream()` and `get_run()`

`stream(run_id)` deliberately consumes the SSE stream to its natural end
(connection close) rather than stopping as soon as it sees a
`type: "run_complete"` or `type: "error"` event. The server always sends
one more synthetic `run_complete` wrapper event after the pipeline's own
last event, and — critically — only sends it *after* the run's terminal
status has been committed to Postgres. Stopping early races that write:
a caller who saw the pipeline's own `"error"` event and immediately
called `get_run()` could get a 404 ("not yet complete") even though the
stream had already reported the run as done. `run_and_wait()` and
`stream()` both wait for the true end, so by the time either returns,
`get_run()` is guaranteed to succeed.

## Testing

`tests/test_client.py` runs against a REAL TrustChain stack — no mocking
of `httpx` or the API:

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
pip install -e ".[dev]"
pytest tests/ -v
```

Automatically skipped if nothing answers at `http://localhost:8000`
(override with the `BASE_URL` constant at the top of the test file).

### Bugs this test suite found

Writing these tests against a real, running stack (rather than mocking
`httpx`) surfaced three real backend bugs, all now fixed:

1. **`redis.exceptions.TimeoutError` escaping unhandled.** It is NOT a
   subclass of the builtin `TimeoutError`, so it slipped past
   `main.py`'s `except TimeoutError:` and silently killed the SSE stream
   mid-run under concurrent load. Fixed in `run_events.py` (map it to the
   builtin `TimeoutError`) and `redis_client.py` (removed the finite
   client-side `socket_timeout` that was racing Redis's own `BLOCK`
   argument in the first place).
2. **This SDK's own `stream()` stopping too early** — see "A note on
   `stream()` and `get_run()`" above.
3. **The real one:** `agents/pipeline.py`'s `run_pipeline()` catches its
   own internal exceptions (an LLM call failing, a tool call failing,
   etc.) and yields a normal `{"type": "error", ...}` event instead of
   raising. `main.py`'s `_run_pipeline_background` only called
   `db.complete_run()` on an explicit `"run_complete"` event and
   otherwise assumed success — neither `db.complete_run()` nor
   `db.fail_run()` was ever called for this case, so the run's row stayed
   at `status='running'` in Postgres **forever**, `GET /runs/{run_id}`
   404'd indefinitely, and the `pipeline_runs_total` Prometheus metric
   recorded it as `"completed"` despite having failed. Fixed in
   `main.py` — see `_run_pipeline_background`'s docstring.
