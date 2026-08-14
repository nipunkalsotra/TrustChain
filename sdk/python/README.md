# trustchain-sdk (Python)

Two things live here, both authenticating with an API key
(`tc_live_.../tc_test_...`, minted via `POST /api-keys`), no human login
required:

1. **`TrustChain`** — instrument YOUR OWN agent, running wherever it
   already runs. This is the actual "any third-party agent can be
   audited through a published SDK" surface.
2. **`TrustChainClient`/`AsyncTrustChainClient`** — a REST wrapper around
   TrustChain's *own* 4-agent pipeline (`POST /run-agent`, SSE streaming,
   trust scores). Useful if you want TrustChain to run agents on your
   behalf, rather than auditing agents you run yourself.

```bash
pip install -e "sdk/python[onchain,langchain]"   # from the repo root, until this is published
```

## Instrumenting your own agent

```python
from trustchain_sdk import TrustChain

tc = TrustChain(api_key="tc_live_...")

# Register the agent's fingerprint once (or whenever its config changes).
# system_prompt is hashed CLIENT-SIDE here — the raw prompt is never sent.
tc.register_agent(
    agent_id="support-bot", model="gpt-4o", version="2026-01",
    system_prompt=SUPPORT_PROMPT,
)

# Log a step explicitly. Non-blocking by default (queues onto a
# background worker thread and returns immediately with step_id=None —
# audit logging must never add latency to your hot path); pass
# wait=True for the synchronous version that returns the real step_id.
result = my_llm_call(query)
tc.log(agent_id="support-bot", action="answer_query", input=query, output=result)

# Or zero lines in the hot path:
@tc.audited(agent_id="support-bot", action="answer_query")
def answer_query(query: str) -> str:
    return my_llm_call(query)

# Call before process exit so any still-queued log() calls aren't lost.
tc.flush()
```

**Framework integration** — audits every LLM/tool call automatically:

```python
from trustchain_sdk.integrations.langchain import TrustChainCallback

agent = create_agent(llm, tools, callbacks=[TrustChainCallback(tc, agent_id="support-bot")])
```

**Verification:**

```python
result = tc.verify_agent("support-bot", model="gpt-4o", version="2026-01", system_prompt=SUPPORT_PROMPT)
# VerifyResult(verified=True, is_active=True, hash_matches=True, ...)
# verified=False means the model/version/prompt changed without re-registering.

proof = tc.get_proof(step_id)          # MerkleProof(leaf, proof, root, tx_hash, ...)
tc.verify_proof(proof)                 # bool — local recompute, no network
tc.verify_proof_onchain(proof, rpc_url=..., audit_log_address=...)  # bool — reads the REAL contract
```

`verify_proof` is a pure local computation (fold the proof into the leaf,
compare to `proof.root`) — meaningful, but it trusts that `proof.root` is
what TrustChain's API says it is. `verify_proof_onchain` is the stronger
form: it reads `AgentAuditLogV2.verifyProof(...)` directly from the chain
at the RPC URL/contract address you provide, so it doesn't have to trust
the API's word for the root at all — only the chain's.

### Failure handling

Every `TrustChain` method fails open by default (`on_error="warn"`, the
constructor's default) — a TrustChain outage degrades to a logged warning
via the `trustchain_sdk` logger, never an exception into your code. Pass
`on_error="raise"` to get real exceptions instead (useful in tests/CI,
where a silently-dropped step should fail loudly).

## Calling TrustChain's own pipeline

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

`tests/test_client.py` and `tests/test_instrumentation.py` run against a
REAL TrustChain stack — no mocking of `httpx` or the API. The
instrumentation tests additionally need real Anvil with V2 deployed
(`register_agent`/`verify_agent`/`get_proof`/`verify_proof_onchain` are
all real on-chain reads/writes, verified end-to-end including a real
`AgentAuditLogV2.verifyProof()` call and its negative case — a forged
leaf that correctly fails to verify):

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
pip install -e ".[dev,onchain]"
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
