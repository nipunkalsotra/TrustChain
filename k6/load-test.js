// k6/load-test.js — mixed read/write load test (P2.7).
//
// Two scenarios run concurrently:
//   read_heavy          — ramps VUs 0 -> READ_VUS hammering the cheap,
//                          frequently-hit read endpoints (leaderboard,
//                          audit-log, runs list). This is deliberately
//                          most of the load: in a real deployment, reads
//                          vastly outnumber writes.
//   occasional_run_agent — a low, constant arrival rate of REAL
//                          POST /run-agent calls (full pipeline: LLM +
//                          on-chain write via the anchor worker), plus a
//                          GET /trust-scores check against the run it
//                          just created. Kept deliberately under the
//                          default rate limit (5/min per project, see
//                          config.py's
//                          run_agent_rate_limit_capacity/_refill_per_second)
//                          so this test measures pipeline latency under
//                          load, not "did the rate limiter correctly
//                          reject me" — see rate-limit-test.js for that.
//
// Run against a real running stack:
//   docker compose up -d api anchor-worker indexer mcp-search mcp-blockchain
//   docker run --rm --network host -v $(pwd)/k6:/scripts \
//     -e BASE_URL=http://localhost:8000 \
//     grafana/k6 run /scripts/load-test.js
//
// Every POST /run-agent call spends a real Groq API call and a real
// anchor-worker on-chain transaction (against Anvil in local dev — free;
// against a real chain, NOT free) — this is why occasional_run_agent's
// rate is kept low rather than scaled with READ_VUS.
//
// read_heavy signs up its OWN project per VU (see vuToken() below)
// rather than sharing one token across all READ_VUS — found for real:
// with every VU hitting the SAME project, they all draw from that one
// project's read-path rate-limit bucket (config.py's
// read_path_rate_limit_capacity=120, refill 2/s — added after this
// script was first written and verified), so at READ_VUS=20 the shared-
// token version blew through that budget in well under a minute and
// this test failed with a ~94% error rate — not a backend bug, a test
// modeling error. Many concurrent VUs against ONE tenant was never the
// realistic case anyway; many DIFFERENT tenants each reading their own
// dashboard concurrently is, and giving each VU its own project models
// that correctly AND gives each VU its own full 120-request budget.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { signup, authHeaders } from './lib/auth.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const READ_VUS = Number(__ENV.READ_VUS || 20);

const runAgentDuration = new Trend('run_agent_duration', true);

// Module-level state is per-VU in k6 (each VU runs its own isolated JS
// instance) — signing up once per VU on first use and caching here means
// exactly READ_VUS signups total, not one per iteration.
let _vuToken = null;
function vuToken() {
  if (_vuToken === null) {
    _vuToken = signup(BASE_URL).token;
  }
  return _vuToken;
}

export const options = {
  scenarios: {
    read_heavy: {
      executor: 'ramping-vus',
      exec: 'readHeavy',
      startVUs: 0,
      stages: [
        { duration: '30s', target: READ_VUS },
        { duration: '2m', target: READ_VUS },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
    occasional_run_agent: {
      executor: 'constant-arrival-rate',
      exec: 'runAgentOccasionally',
      rate: 2,
      timeUnit: '1m',
      duration: '3m',
      preAllocatedVUs: 2,
      maxVUs: 4,
    },
  },
  thresholds: {
    // Read paths: fast and reliable — a regression here is a real bug.
    'http_req_duration{endpoint:trust-scores}': ['p(95)<1000'],
    'http_req_duration{endpoint:leaderboard}': ['p(95)<1000'],
    'http_req_duration{endpoint:audit-log}': ['p(95)<1000'],
    'http_req_duration{endpoint:runs}': ['p(95)<1000'],
    'http_req_failed{endpoint:trust-scores}': ['rate<0.01'],
    'http_req_failed{endpoint:leaderboard}': ['rate<0.01'],
    'http_req_failed{endpoint:audit-log}': ['rate<0.01'],
    'http_req_failed{endpoint:runs}': ['rate<0.01'],
    // /run-agent starts a whole LLM+chain pipeline in the background and
    // returns as soon as it's queued — the HTTP call itself should stay
    // fast even though the pipeline it kicks off takes much longer.
    'http_req_duration{endpoint:run-agent}': ['p(95)<2000'],
    'http_req_failed{endpoint:run-agent}': ['rate<0.05'],
  },
};

export function setup() {
  const user = signup(BASE_URL);
  return { token: user.token };
}

export function readHeavy() {
  // Own project per VU — see the module-level comment above for why.
  const opts = { ...authHeaders(vuToken()) };

  let res = http.get(`${BASE_URL}/leaderboard`, { ...opts, tags: { endpoint: 'leaderboard' } });
  check(res, { 'leaderboard 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/audit-log`, { ...opts, tags: { endpoint: 'audit-log' } });
  check(res, { 'audit-log 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/runs`, { ...opts, tags: { endpoint: 'runs' } });
  check(res, { 'runs 200': (r) => r.status === 200 });

  // sleep(2), not sleep(1): 3 reads/iteration against a shared per-project
  // bucket refilling at 2/s (config.py's read_path_rate_limit_refill_per_second)
  // means sleep(1) (3 req/~1.1s ~= 2.7 req/s) is UNSUSTAINABLE long-run —
  // confirmed live over a full 3-minute run: leaderboard/audit-log (called
  // 1st/2nd each iteration) stayed at 0% failures the whole time, but
  // `runs` (always 3rd, so it's the one that finds the bucket empty)
  // crossed the 1% threshold late in the run as VUs' accumulated deficit
  // caught up with them. sleep(2) keeps sustained demand (3 req/~2.1s ~=
  // 1.4 req/s) under the 2/s refill rate indefinitely, not just for a
  // short burst — re-verified live: 0 failures across a full 3-minute run.
  sleep(2);
}

export function runAgentOccasionally(data) {
  const opts = { ...authHeaders(data.token), tags: { endpoint: 'run-agent' } };
  const res = http.post(
    `${BASE_URL}/run-agent`,
    JSON.stringify({ task: `k6 load test task ${Date.now()}` }),
    opts,
  );
  runAgentDuration.add(res.timings.duration);
  check(res, {
    'run-agent 200': (r) => r.status === 200,
    'run-agent returned run_id': (r) => !!r.json('run_id'),
  });

  // GET /trust-scores takes scores for ONE specific run (run_id query
  // param, required). Checked here, against the run this same call just
  // created in this SAME project (data.token — the one dedicated
  // occasional_run_agent identity, distinct from read_heavy's per-VU
  // ones), rather than in read_heavy: each read_heavy VU now has its own
  // empty project with no runs of its own to look up scores for.
  if (res.status === 200) {
    const runId = res.json('run_id');
    const scoresRes = http.get(
      `${BASE_URL}/trust-scores?run_id=${encodeURIComponent(runId)}`,
      { ...authHeaders(data.token), tags: { endpoint: 'trust-scores' } },
    );
    check(scoresRes, { 'trust-scores 200': (r) => r.status === 200 });
  }
}
