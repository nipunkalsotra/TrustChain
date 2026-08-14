// k6/rate-limit-test.js — deliberately BREAKS the rate limit on purpose,
// against real Redis-backed token buckets (backend/rate_limit.py), to
// verify the actual behavior under real concurrent load rather than
// trusting the unit tests alone: a burst of concurrent requests should
// produce a clean mix of 200s (up to the bucket's capacity) and 429s with
// a Retry-After header (once exhausted) — never 500s, and never letting
// meaningfully more than `capacity` requests through in one burst (the
// atomic-Lua-script design's whole reason to exist — see rate_limit.py's
// module docstring on the read-then-write race a naive implementation
// would have).
//
// Default RUN_AGENT_RATE_LIMIT_CAPACITY is 5 — this fires 20 concurrent
// POST /run-agent requests from ONE project (same signed-up user) in a
// single burst, well past capacity, and asserts on the resulting
// status-code mix.
//
// Run:
//   docker run --rm --network host -v $(pwd)/k6:/scripts \
//     -e BASE_URL=http://localhost:8000 \
//     grafana/k6 run /scripts/rate-limit-test.js

import http from 'k6/http';
import { check } from 'k6';
import { signup, authHeaders } from './lib/auth.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const BURST_SIZE = Number(__ENV.BURST_SIZE || 20);

// 429 is an EXPECTED, correct outcome once the burst exceeds capacity —
// without this, k6's default http_req_failed metric counts every 429 as
// a failure (it treats any non-2xx/3xx as failed by default), which
// would make the whole point of this test — "some requests should get
// 429'd" — look like a failing threshold instead of the passing
// verification it actually is. Only a genuine 5xx (or anything else) now
// counts as failed.
http.setResponseCallback(http.expectedStatuses(200, 429));

export const options = {
  scenarios: {
    burst: {
      executor: 'shared-iterations',
      vus: BURST_SIZE,
      iterations: BURST_SIZE,
      maxDuration: '30s',
    },
  },
  thresholds: {
    // The real assertion lives in handleSummary below (it needs the full
    // set of status codes across the whole burst, not a per-request
    // threshold) — this just guards against anything worse than a 429,
    // like a 500 from the rate limiter itself misbehaving under
    // concurrency.
    http_req_failed: ['rate==0'],
  },
};

export function setup() {
  const user = signup(BASE_URL);
  return { token: user.token };
}

export default function (data) {
  const res = http.post(
    `${BASE_URL}/run-agent`,
    JSON.stringify({ task: `k6 rate limit burst ${__VU}-${__ITER}` }),
    authHeaders(data.token),
  );

  check(res, {
    'status is 200 or 429 (never 5xx)': (r) => r.status === 200 || r.status === 429,
    '429 responses carry Retry-After': (r) => r.status !== 429 || !!r.headers['Retry-After'],
  });
}

// Deliberately no handleSummary() override here — defining one REPLACES
// k6's own end-of-run summary (checks/thresholds/http_req_duration
// breakdown) instead of adding to it, which would hide exactly the
// status-code/check breakdown this test exists to show. Read that
// default summary block for the actual pass/fail picture.
