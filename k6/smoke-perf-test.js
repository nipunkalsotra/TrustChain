// k6/smoke-perf-test.js — lightweight read-path performance gate for
// push/PR CI (P2.7's "wire k6 into CI" gap).
//
// load-test.js/soak-test.js stay scheduled+manual only (see k6.yml's own
// comment): a genuine 3-10 minute sustained-traffic run isn't something
// every push/PR should pay for, and shared GitHub Actions runners are
// noisy enough neighbors to make that class of test flaky there. This
// script is deliberately a different, much cheaper thing: ~35 seconds
// total, a handful of VUs, read-only (no POST /run-agent — that spends a
// real LLM call and a real on-chain transaction per request, which has
// no place running on every single push). It exists to catch a gross
// latency regression on the cheap, frequently-hit read endpoints fast,
// not to be a load test.
//
// Run against a real running stack:
//   docker compose up -d api anchor-worker indexer mcp-search mcp-blockchain
//   docker run --rm --network host -v $(pwd)/k6:/scripts \
//     -e BASE_URL=http://localhost:8000 \
//     grafana/k6 run /scripts/smoke-perf-test.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { signup, authHeaders } from './lib/auth.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  scenarios: {
    smoke: {
      executor: 'ramping-vus',
      exec: 'readSmoke',
      startVUs: 0,
      stages: [
        { duration: '5s', target: 3 },
        { duration: '15s', target: 3 },
        { duration: '5s', target: 0 },
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    // Same latency bar as load-test.js's read paths — this is a shorter,
    // lighter run of the same claim ("these endpoints stay fast"), not a
    // looser one.
    'http_req_duration{endpoint:leaderboard}': ['p(95)<1000'],
    'http_req_duration{endpoint:audit-log}': ['p(95)<1000'],
    'http_req_duration{endpoint:runs}': ['p(95)<1000'],
    'http_req_failed{endpoint:leaderboard}': ['rate<0.01'],
    'http_req_failed{endpoint:audit-log}': ['rate<0.01'],
    'http_req_failed{endpoint:runs}': ['rate<0.01'],
  },
};

export function setup() {
  const user = signup(BASE_URL);
  return { token: user.token };
}

export function readSmoke(data) {
  const opts = { ...authHeaders(data.token) };

  let res = http.get(`${BASE_URL}/leaderboard`, { ...opts, tags: { endpoint: 'leaderboard' } });
  check(res, { 'leaderboard 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/audit-log`, { ...opts, tags: { endpoint: 'audit-log' } });
  check(res, { 'audit-log 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/runs`, { ...opts, tags: { endpoint: 'runs' } });
  check(res, { 'runs 200': (r) => r.status === 200 });

  // sleep(2), not sleep(1): all VUs share ONE signed-up user/project (see
  // setup() below), so their requests all draw from the SAME read-path
  // rate-limit bucket (config.py's read_path_rate_limit_capacity=120,
  // refill 2/s — see db/tenancy.py's per-project token bucket, keyed by
  // project_id in rate_limit.enforce_read_rate_limit). At 3 VUs x 3
  // requests/iteration, sleep(1) would burn through that budget inside
  // ~13s and start seeing real 429s — not a backend bug, just this
  // script asking for more read throughput than one project's budget
  // allows. sleep(2) keeps total requests for this whole run comfortably
  // under 120 even ignoring refill — verified locally (0 rate-limit
  // rejections over a full run at this rate).
  sleep(2);
}
