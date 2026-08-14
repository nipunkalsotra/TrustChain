// k6/load-test.js — mixed read/write load test (P2.7).
//
// Two scenarios run concurrently:
//   read_heavy          — ramps VUs 0 -> READ_VUS hammering the cheap,
//                          frequently-hit read endpoints (trust-scores,
//                          leaderboard, audit-log, runs list, health).
//                          This is deliberately most of the load: in a
//                          real deployment, reads vastly outnumber writes.
//   occasional_run_agent — a low, constant arrival rate of REAL
//                          POST /run-agent calls (full pipeline: LLM +
//                          on-chain write via the anchor worker). Kept
//                          deliberately under the default rate limit
//                          (5/min per project, see config.py's
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

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { signup, authHeaders } from './lib/auth.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const READ_VUS = Number(__ENV.READ_VUS || 20);

const runAgentDuration = new Trend('run_agent_duration', true);

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

export function readHeavy(data) {
  const opts = { ...authHeaders(data.token) };

  let res = http.get(`${BASE_URL}/leaderboard`, { ...opts, tags: { endpoint: 'leaderboard' } });
  check(res, { 'leaderboard 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/audit-log`, { ...opts, tags: { endpoint: 'audit-log' } });
  check(res, { 'audit-log 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/runs`, { ...opts, tags: { endpoint: 'runs' } });
  check(res, { 'runs 200': (r) => r.status === 200 });

  // GET /trust-scores takes scores for ONE specific run (run_id query
  // param, required) — it's not a listing endpoint like the three above.
  // Only exercise it once a run actually exists to fetch scores for
  // (occasional_run_agent creates them; early in the test, before any
  // have landed, there may be none yet — that's a legitimate "nothing to
  // check" iteration, not a failure).
  const runs = res.status === 200 ? res.json('runs') : [];
  if (runs && runs.length > 0) {
    const runId = runs[0].runId;
    const scoresRes = http.get(
      `${BASE_URL}/trust-scores?run_id=${encodeURIComponent(runId)}`,
      { ...opts, tags: { endpoint: 'trust-scores' } },
    );
    check(scoresRes, { 'trust-scores 200': (r) => r.status === 200 });
  }

  sleep(1);
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
}
