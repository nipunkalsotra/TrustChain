// k6/soak-test.js — sustained, low-but-constant load over a long
// duration (P2.7), looking for degradation over time rather than
// breaking point: growing latency, memory leaks (watch container RSS via
// `docker stats` or the Grafana dashboard alongside this run), connection
// pool exhaustion, slow log/disk growth.
//
// DURATION defaults to 10 minutes — long enough to catch a fast leak or
// an obviously-growing latency trend locally, but this is NOT a
// substitute for a real multi-hour (or overnight) soak against a staging
// environment before trusting a "no leaks" conclusion; a slow leak with a
// large per-request footprint can easily take longer than 10 minutes to
// become visible. Override via DURATION (k6 duration string, e.g. "2h").
//
// Run:
//   docker run --rm --network host -v $(pwd)/k6:/scripts \
//     -e BASE_URL=http://localhost:8000 -e DURATION=10m \
//     grafana/k6 run /scripts/soak-test.js
//
// While it runs, watch:
//   - Grafana (localhost:3002, "TrustChain" dashboard) for latency/error
//     trending upward over the run instead of staying flat.
//   - `docker stats api anchor-worker indexer` for memory that only ever
//     goes up, never plateaus.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { signup, authHeaders } from './lib/auth.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const DURATION = __ENV.DURATION || '10m';
const VUS = Number(__ENV.VUS || 5);

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-vus',
      exec: 'readLoop',
      vus: VUS,
      duration: DURATION,
    },
    // A slow trickle of real pipeline runs, low enough to stay well
    // under the default rate limit regardless of DURATION, so the
    // read loop's /trust-scores check has real runs to fetch scores for
    // over a long soak instead of always hitting the "no runs yet" branch.
    occasional_run_agent: {
      executor: 'constant-arrival-rate',
      exec: 'runAgentOccasionally',
      rate: 1,
      timeUnit: '2m',
      duration: DURATION,
      preAllocatedVUs: 1,
      maxVUs: 2,
    },
  },
  thresholds: {
    // Loose, absolute thresholds are the wrong tool for "is it
    // degrading" — that's a trend you read off the dashboard during/after
    // the run, not a single number. These just catch outright breakage.
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(99)<5000'],
  },
};

export function setup() {
  const user = signup(BASE_URL);
  return { token: user.token };
}

export function runAgentOccasionally(data) {
  const res = http.post(
    `${BASE_URL}/run-agent`,
    JSON.stringify({ task: `k6 soak test task ${Date.now()}` }),
    authHeaders(data.token),
  );
  check(res, { 'run-agent 200': (r) => r.status === 200 });
}

export function readLoop(data) {
  const opts = authHeaders(data.token);

  let res = http.get(`${BASE_URL}/runs`, opts);
  check(res, { 'runs 200': (r) => r.status === 200 });

  // GET /trust-scores needs a specific run_id (query param, required) —
  // only call it once this soak's own run-agent iterations (below) have
  // produced at least one run to fetch scores for.
  const runs = res.status === 200 ? res.json('runs') : [];
  if (runs && runs.length > 0) {
    const scoresRes = http.get(`${BASE_URL}/trust-scores?run_id=${encodeURIComponent(runs[0].runId)}`, opts);
    check(scoresRes, { 'trust-scores 200': (r) => r.status === 200 });
  }

  res = http.get(`${BASE_URL}/leaderboard`, opts);
  check(res, { 'leaderboard 200': (r) => r.status === 200 });

  res = http.get(`${BASE_URL}/health`);
  check(res, { 'health 200': (r) => r.status === 200 });

  sleep(2);
}
