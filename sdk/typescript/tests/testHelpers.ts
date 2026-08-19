/**
 * tests/testHelpers.ts — shared helpers for this SDK's own real-backend
 * integration tests (no mocking — see each test file's own docstring).
 *
 * Phase 4 G1 added a real email-verification gate
 * (backend/permissions.py::REQUIRES_VERIFIED_EMAIL) in front of
 * POST /api-keys — every `freshApiKey()`-style helper that signs up a
 * fresh user and immediately needs to mint an API key now 403s with
 * `email_not_verified` unless that user is verified first.
 * `verifiedSignup()` is the one place that flow lives, so every call
 * site (previously 2 separate near-duplicates across
 * client.test.ts/instrumentation.test.ts, mirroring the same duplication
 * the Python SDK's own test suite had before its own Phase 4 fix) picks
 * it up identically.
 */

import assert from "node:assert/strict";
import pg from "pg";

/**
 * Signs up a real user over real HTTP (exactly as before), then marks
 * their email verified via a direct database write rather than a real
 * HTTP round trip through POST /auth/verify-email/{token}.
 *
 * Why not go through the real endpoint: the raw verification token only
 * ever exists in the email it was sent in (only sha256(token) is ever
 * stored — ADR-0014/0022), and this test process has no reliable way to
 * read whichever email transport the backend under test is using — CI's
 * sdk-integration job runs the backend via `docker compose up`, whose
 * logs live in Docker's own log buffer, not a file this process can
 * read (the Python SDK's own equivalent fix hit this exact same wall —
 * see sdk/python/tests/conftest.py's docstring for the fuller version of
 * this reasoning). A direct DB write sidesteps needing to know which
 * email transport is in play at all, and needs no dependency this repo
 * doesn't already effectively assume (docker-compose's postgres service
 * is already a hard requirement of running these tests at all).
 *
 * This is a deliberate, narrow exception to this suite's real-HTTP-only
 * testing philosophy, not a precedent for testing everything this way:
 * the verification MECHANISM itself (the token round trip through
 * /auth/verify-email/{token}) is already exercised for real by
 * backend/tests/test_email_verification.py. This suite's actual job is
 * proving the SDK's HTTP client talks correctly to a live API — a job
 * the verification gate would otherwise block from running at all if
 * every helper had to solve real email delivery first.
 */
export async function verifiedSignup(
  baseUrl: string,
  name: string,
  email: string,
  password: string,
): Promise<string> {
  const signupRes = await fetch(`${baseUrl}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  });
  const signupBody = await signupRes.text();
  assert.equal(signupRes.status, 200, signupBody);
  const { token } = JSON.parse(signupBody) as { token: string };

  await markVerified(email);
  return token;
}

// Same well-known docker-compose credentials/port CLAUDE.md and this
// repo's own local-dev conventions use everywhere else (see
// docker-compose.yml's postgres service) — overridable via DATABASE_URL
// for a non-default setup. `pg` wants a plain postgresql:// DSN, not
// SQLAlchemy's postgresql+asyncpg:// dialect prefix backend/.env's own
// DATABASE_URL uses, so that prefix is stripped if present.
function connectionString(): string {
  const raw = process.env.DATABASE_URL;
  if (raw) return raw.replace("postgresql+asyncpg://", "postgresql://");
  return "postgresql://trustchain:trustchain@localhost:5432/trustchain";
}

async function markVerified(email: string): Promise<void> {
  const client = new pg.Client({ connectionString: connectionString() });
  await client.connect();
  try {
    await client.query("UPDATE users SET email_verified = true WHERE email = $1", [email]);
  } finally {
    await client.end();
  }
}
