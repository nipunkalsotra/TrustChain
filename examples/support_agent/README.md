# Support agent example

A small, realistic customer-support agent instrumented with the
TrustChain Python SDK (`sdk/python`) — Phase 4 plan §3 step 9.

Not a toy that logs `"test"` in a loop: `agent.py`'s `_KNOWLEDGE_BASE`
holds real-shaped support answers (order numbers, dollar amounts, dates)
so the content it logs is believable enough for `scripts/e2e_demo.py`'s
later tamper/`verify-content` stages to confirm against.

## Run it standalone

```bash
cd sdk/python && pip install -e .   # once, if not already installed
cd ../..                            # back to repo root

export TRUSTCHAIN_API_KEY=tc_live_...   # see docs/e2e-walkthrough.md for how to mint one
export TRUSTCHAIN_BASE_URL=http://localhost:8000   # default, shown for clarity

python3 -m examples.support_agent.agent
```

Expected output:

```
step_id: 42
proof verifies: True
classified intent (logged via @tc.audited): return_policy
```

## What it demonstrates

- `SupportAgent.connect()` — `tc.register_agent(...)`, once, before
  anything logs a step under this agent's identity.
- `SupportAgent.answer()` — the blocking `tc.log_and_wait(...)` form,
  used because the caller needs the real `step_id` back immediately (to
  fetch a Merkle proof, or point a tamper demo at this exact row).
- `SupportAgent.classify_intent()` via `_make_classify_intent_audited` —
  the non-blocking `@tc.audited(...)` decorator form: one line to
  instrument an existing function, fire-and-forget.
- `tc.get_proof(...)` + `tc.verify_proof(...)` — fetching and locally
  verifying a Merkle inclusion proof for a logged step.

`scripts/e2e_demo.py` drives this same `SupportAgent` class as one stage
of the full signup → tamper → email → content-verification walkthrough —
see [`docs/e2e-walkthrough.md`](../../docs/e2e-walkthrough.md).
