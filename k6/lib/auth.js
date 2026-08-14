// k6/lib/auth.js — shared signup helper for the load/soak/rate-limit
// scripts. Each k6 run signs up ONE fresh user (unique email per run,
// via a random suffix) rather than reusing a fixed test account, so
// concurrent/repeated runs never collide on "email already registered".

import http from 'k6/http';
import { check } from 'k6';

export function signup(baseUrl) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e9)}`;
  const email = `k6_${suffix}@example.com`;
  const password = 'k6-load-test-password-not-real';

  const res = http.post(
    `${baseUrl}/auth/signup`,
    JSON.stringify({ name: 'k6 load test', email, password }),
    { headers: { 'Content-Type': 'application/json' } },
  );

  check(res, {
    'signup succeeded': (r) => r.status === 200,
    'signup returned a token': (r) => !!r.json('token'),
  });

  if (res.status !== 200) {
    throw new Error(`setup: signup failed (${res.status}): ${res.body}`);
  }

  return { email, password, token: res.json('token') };
}

export function authHeaders(token) {
  return { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } };
}
