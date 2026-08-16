# k6 load / soak / rate-limit / smoke tests (P2.7)

Four scripts, each independently runnable against a real running stack —
none of these are mocks; every one drives real HTTP requests against a
real API process backed by real Postgres/Redis/Anvil (and, for
`/run-agent`, a real Groq LLM call and a real anchor-worker on-chain
transaction).

| Script | What it checks | Runs in CI |
|---|---|---|
| `smoke-perf-test.js` | ~35s, read-only, small VU count — a fast p95-latency/error-rate gate on the cheap read endpoints (leaderboard/audit-log/runs). | Every push/PR (`test.yml`'s `sdk-integration` job) |
| `load-test.js` | Mixed realistic traffic: ramping reads (leaderboard/audit-log/runs/trust-scores) plus a low, rate-limit-respecting trickle of real `POST /run-agent` calls. Fails the run if p95 latency or error rate cross the thresholds defined in the script. | Nightly + manual (`k6.yml`) |
| `soak-test.js` | Sustained constant load over a long duration, looking for degradation over time (growing latency, memory) rather than a breaking point. | Nightly + manual (`k6.yml`) |
| `rate-limit-test.js` | Deliberately bursts past the default rate limit (20 concurrent `POST /run-agent` from one project, capacity is 5) and asserts the real behavior: a clean mix of 200/429, `Retry-After` present on every 429, never a 5xx. | Manual only (`k6.yml`) |

`smoke-perf-test.js` is deliberately a different, much cheaper thing than
the other three, not a shorter `load-test.js` — no `/run-agent` calls (a
real LLM + on-chain cost on every push has no place in that gate), a
fraction of the VUs, and a fraction of the duration. See `k6.yml`'s and
`smoke-perf-test.js`'s own header comments for the full reasoning behind
keeping the other three scheduled/manual-only.

## Prerequisites

A running stack with the API reachable and V2 contracts deployed:

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
```

If Anvil was just (re)started fresh, redeploy V2 and record its addresses
first (see `.github/workflows/test.yml`'s backend job for the exact
sequence: `forge build` → `extract_v2_abis.py` → `forge script
script/DeployV2.s.sol --broadcast` → `write_v2_addresses.py` → `alembic
upgrade head`) — the API will otherwise return chain-related errors on
anything that touches on-chain state.

## Running

No local k6 install needed — run via the official Docker image:

```bash
docker run --rm --network host -v $(pwd)/k6:/scripts \
  -e BASE_URL=http://localhost:8000 \
  grafana/k6 run /scripts/smoke-perf-test.js

docker run --rm --network host -v $(pwd)/k6:/scripts \
  -e BASE_URL=http://localhost:8000 \
  grafana/k6 run /scripts/load-test.js

docker run --rm --network host -v $(pwd)/k6:/scripts \
  -e BASE_URL=http://localhost:8000 -e DURATION=10m -e VUS=5 \
  grafana/k6 run /scripts/soak-test.js

docker run --rm --network host -v $(pwd)/k6:/scripts \
  -e BASE_URL=http://localhost:8000 \
  grafana/k6 run /scripts/rate-limit-test.js
```

Watch Grafana (`:3002`, dashboard "TrustChain" — see
`docker/grafana/dashboards/trustchain.json`) alongside any of these for
the metrics-eye view of the same run.

## What's been verified locally vs. what these are for

`smoke-perf-test.js` was run for real against the live local stack. The
first version (3 VUs, `sleep(1)`) surfaced a real interaction, not a
backend bug: all 3 VUs share one signed-up user/project, so their reads
draw from the SAME read-path rate-limit bucket
(`config.py`'s `read_path_rate_limit_capacity=120`, refill 2/s) — that
version pushed ~400 requests through a single project's ~190-request
budget over 35s and correctly got a wave of real 429s back
(`http_req_failed` around 54%). Fixed by slowing the script to `sleep(2)`
(see its own comment); a rerun at that rate completed 94 requests with
100% of checks passing and every threshold green, 0 rate-limit
rejections.

`load-test.js` and `rate-limit-test.js` have been run for real against
the full local stack. `load-test.js`'s original verification (15
concurrent VUs sharing one project's token, 100% green) predates
`config.py`'s read-path rate limiter — a later full 3-minute rerun at
READ_VUS=20 against that limiter surfaced a real interaction, not a
backend bug: every VU sharing one project meant they all drew from that
one project's read-path bucket (capacity 120, refill 2/s), which is
sustainable for one VU but not 20 at `sleep(1)` — 94% of checks failed.
Fixed by giving each `read_heavy` VU its own signed-up project
(spreading load the way real multi-tenant traffic actually would) and
slowing to `sleep(2)` after a second live run showed the shared-bucket
call order (`runs`, called 3rd each iteration, right after
`leaderboard`/`audit-log`) still crossed threshold late in a 3-minute
run at `sleep(1)`'s pace. A third full 3-minute run at `sleep(2)`
completed 4,283 requests with 0 failures start to finish. See
`load-test.js`'s own header/inline comments for the full reasoning.

A 20-request concurrent burst against `POST /run-agent` (`rate-limit-test.js`)
confirmed the real Redis-backed token bucket (`backend/rate_limit.py`)
correctly lets a subset through and 429s the rest with `Retry-After`, no
5xxs — see `rate_limit_rejections_total`/`pipeline_runs_total` in
`/metrics` for the same result from the server side.

`soak-test.js` was run for a shortened 2-minute smoke pass (not its
10-minute default) with container memory sampled every ~35s — flat
across samples, no evidence of a fast leak. A 2-minute (or even 10-minute)
run is NOT a substitute for a real multi-hour or overnight soak against a
staging environment before trusting a "no leaks" conclusion — a slow
leak with a small per-request footprint can easily take far longer than
that to become visible. Run it for real, for real hours, before relying
on it as a release gate.

## A bug this caught

Earlier versions of `load-test.js`/`soak-test.js` called
`GET /trust-scores` with no query parameters, assuming it was a listing
endpoint like `/leaderboard`. It isn't — it returns scores for ONE
specific run (`run_id` is a required query param) — so every single call
failed with a 422, and the load test correctly flagged 100% failure on
that endpoint. That was a k6 script bug, not a backend bug (confirmed by
calling it directly with `curl` and reading `main.py`'s actual route
signature), but it's a good example of what these tests are for: a
misunderstanding about an endpoint contract, caught by hitting it for
real instead of trusting an assumption.
