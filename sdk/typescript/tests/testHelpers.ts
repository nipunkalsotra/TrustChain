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
  // SDK integration suites create many real accounts from one CI runner IP.
  // Signup correctly applies a shared IP token bucket, independent of the
  // per-project rate-limit tests. Respect its Retry-After instead of failing
  // unrelated SDK assertions when earlier tests have exhausted that bucket.
  // Only 429 responses are retried; all other failures still fail immediately.
  let token: string | undefined;
  for (let attempt = 0; attempt < 10; attempt++) {
    const signupRes = await fetch(`${baseUrl}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password }),
    });
    const signupBody = await signupRes.text();
    if (signupRes.status === 200) {
      token = (JSON.parse(signupBody) as { token: string }).token;
      break;
    }
    if (signupRes.status !== 429 || attempt === 9) {
      assert.equal(signupRes.status, 200, signupBody);
    }
    const retryAfter = Number(signupRes.headers.get("Retry-After"));
    // Retry-After is the API contract; bound invalid/missing values.
    const delayMs = Number.isFinite(retryAfter) && retryAfter > 0
      ? Math.min(retryAfter * 1000, 10_000)
      : 2_000;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  assert.ok(token, "signup did not succeed within the bounded rate-limit retries");

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

/**
 * Directly overwrites a step's stored output_hash — the same raw-SQL
 * tamper backend/tests/test_verify_content.py and the Python SDK's own
 * conftest.py::tamper_step_output_hash use to exercise the "after
 * tampering" path for TrustChain.verifyContent. Same deliberate, narrow
 * exception to this suite's real-HTTP-only philosophy as
 * verifiedSignup() above, not a new pattern — there is no legitimate
 * HTTP endpoint that does this by design (docs/adr/0020).
 */
export async function tamperStepOutputHash(stepId: number): Promise<void> {
  const client = new pg.Client({ connectionString: connectionString() });
  await client.connect();
  try {
    await client.query("UPDATE steps SET output_hash = '0x' || repeat('f', 64) WHERE id = $1", [stepId]);
  } finally {
    await client.end();
  }
}
