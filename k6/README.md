# k6 load / soak / rate-limit tests (P2.7)

Three scripts, each independently runnable against a real running stack —
none of these are mocks; every one drives real HTTP requests against a
real API process backed by real Postgres/Redis/Anvil (and, for
`/run-agent`, a real Groq LLM call and a real anchor-worker on-chain
transaction).

| Script | What it checks |
|---|---|
| `load-test.js` | Mixed realistic traffic: ramping reads (leaderboard/audit-log/runs/trust-scores) plus a low, rate-limit-respecting trickle of real `POST /run-agent` calls. Fails the run if p95 latency or error rate cross the thresholds defined in the script. |
| `soak-test.js` | Sustained constant load over a long duration, looking for degradation over time (growing latency, memory) rather than a breaking point. |
| `rate-limit-test.js` | Deliberately bursts past the default rate limit (20 concurrent `POST /run-agent` from one project, capacity is 5) and asserts the real behavior: a clean mix of 200/429, `Retry-After` present on every 429, never a 5xx. |

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

`load-test.js` and `rate-limit-test.js` have been run for real against
the full local stack: a 3-minute mixed-traffic load test at 15 concurrent
VUs (~46 req/s) passed 100% of checks with all latency/error thresholds
green, and a 20-request concurrent burst against `POST /run-agent`
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
