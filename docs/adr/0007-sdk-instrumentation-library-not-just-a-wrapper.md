# 0007 — SDKs are an instrumentation library, not just a REST wrapper

**Status:** Accepted

## Context

An earlier iteration of the Python/TypeScript SDKs (`TrustChainClient`)
was a thin REST wrapper around TrustChain's *own* pipeline — good for
driving TrustChain's 4-agent pipeline programmatically, but it gave a
third party no way to audit *their own* agent through TrustChain. The
actual product promise ("any third-party agent can be audited through a
published SDK") requires letting an external caller register their
agent's identity, log their own steps, and verify proofs — none of
which "wrap our own pipeline's REST API" achieves.

## Decision

Both SDKs ship **two** classes with a clear division of responsibility:

- **`TrustChain`** (`instrumentation.py`/`.ts`) — instrument a
  third-party agent running wherever it already runs: `register_agent()`
  (hashes the live system prompt/model/version **client-side**, so the
  raw prompt is never transmitted), `log()` (non-blocking — returns a
  receipt immediately, the real API call happens on a background
  thread/fire-and-forget promise), `verify_agent()`, `get_proof()` +
  `verify_proof()` (local recompute) / `verify_proof_onchain()` (reads
  the real deployed contract directly, trusting nothing from
  TrustChain's own API). A LangChain callback handler
  (`TrustChainCallback`) audits every LLM/tool call automatically.
- **`TrustChainClient`** — the original thin wrapper, kept, for driving
  TrustChain's own pipeline (`run_agent`, SSE streaming, trust scores).

Every method on `TrustChain` fails open by default (`on_error="warn"` —
swallow and log, never raise into the caller's application code) unless
explicitly configured otherwise; audit logging must never be able to
break the host application it's instrumenting.

## Alternatives considered

- **One class doing both jobs.** Rejected: the two have genuinely
  different failure semantics (a third party's own agent must never be
  broken by an audit-logging failure; driving TrustChain's own pipeline
  is a direct, synchronous, should-raise-on-error API call) and
  conflating them would force one semantics onto the other for no
  benefit.
- **Server-side prompt hashing** (send the raw prompt, let the backend
  hash it). Rejected: defeats the actual security property being
  offered — a third party auditing their own agent shouldn't have to
  trust TrustChain with their system prompt at all, only with a hash of
  it.
- **Synchronous-only `log()`.** Rejected as the default: coupling every
  instrumented call to a network round-trip before the host
  application can proceed is exactly the latency risk `on_error="warn"`
  and non-blocking `log()` both exist to avoid. `log_and_wait()` is
  offered as the explicit opt-in for callers that need the real
  `step_id` immediately (e.g. right before calling `get_proof()`).

## Consequences

- Cross-language design had to be translated, not copy-pasted:
  Python's non-blocking `log()` uses a `threading.Thread` +
  `queue.Queue` worker; TypeScript's uses fire-and-forget promises
  tracked in a `Set<Promise<void>>` for `flush()` to await. Same
  contract, idiomatic implementation per language.
- `web3`/`viem` (on-chain proof verification) and `langchain-core`
  (the callback integration) are optional extras/peer dependencies in
  both SDKs, not base dependencies — most SDK consumers need neither,
  and pulling in a full web3 stack just to log steps would be a poor
  default.
- The two-class split means a consumer has to understand which one
  they want before writing code against either — documented explicitly
  in both READMEs' opening section specifically to head this off.
