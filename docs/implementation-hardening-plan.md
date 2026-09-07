# Production-readiness hardening plan

Execution plan for the security/reliability/delivery hardening pass
requested in `hardening/trustchain-production-readiness`. Written after
auditing the live repo, not the spec's assumptions — two things the spec
got wrong about current state are corrected below.

## Corrections to the source spec's assumptions

- **PR #52 (`landingpage`) is not open.** It was already merged into
  `main` (commit `0392491`) before this branch was created. The
  cinematic 3D landing implementation and its dependencies
  (`frontend/components/landing/three/`, `@react-three/*`, `gsap`,
  `lenis`) are live on `main` today. Phase 2 below is "remove from
  `main` now", not "clean up before merge."
- **`gh` CLI is not installed in this environment** and there is no
  interactive GitHub auth available. Branch protection and CODEOWNERS
  *enforcement* are repo-admin Settings actions this session cannot take
  — a manual checklist is provided (§3) instead of a claimed change.
  `CODEOWNERS` (the file, not the enforcement toggle) is created as
  part of this branch since that part is just a committed file.

## Status by phase

| Phase | Item | Status |
|---|---|---|
| 1 | Audit + this plan | Done |
| 2 | Remove cinematic 3D landing code from `main` | Done — verified: `tsc --noEmit` clean, `eslint` clean (same 3 pre-existing warnings), `next build` compiles all 11 routes incl. `/pricing`/`/coming-soon`, 59 packages removed, zero remaining `three`/`gsap`/`lenis`/`@react-three` references anywhere in source or lockfile |
| 3 | Branch protection checklist, CODEOWNERS, release workflow fix | Done — see §3 below for the manual checklist and the real root cause found |
| 4 | Infra isolation (compose split, port binding, redactions) | Done — see §4 below |
| 5 | Signed stream token for `GET /stream/{run_id}` | Done — see §5 below |
| 6 | Cookie-based browser auth + CSRF | Done — see §6 below |
| 7 | EvidencePublisher (manifest, no-PII, pluggable adapter) | Done — see §7 below |
| 8 | README replacement | Done — see §8 below |
| 9 | CI gate audit, dependency upgrades | Done — see §9 below |
| 10 | Bounded refactor + revocation-semantics fix + tests | Partial — see §10 below (tests + revocation fix done; the router/module split deliberately NOT attempted) |
| 11 | Verification + final report | Done — see below |

## §11 — Final verification

- **Backend**: `pytest -q` (`TESTCONTAINERS_RYUK_DISABLED=true`, real
  Postgres/Redis via Testcontainers) — **408 passed, 3 skipped
  (pre-existing, unrelated), 0 failed**, 500s. `ruff check .` clean.
  `ruff format`/`mypy` clean against their committed baselines (117/117
  and 82/82 respectively — no new violations). `bandit -r . --severity-
  level medium`: 0 findings.
- **Frontend**: `tsc --noEmit` clean. `eslint`: 0 errors, 3 pre-existing
  warnings (unchanged all session). `npm run test` (vitest): 17/17. `npm
  run build`: all 11 routes compile. `npx playwright test`: 4/4 against
  a real backend + real headless Chromium.
- **Contracts**: untouched this entire session (confirmed via `git
  status contracts/` — clean) — no redeploy, no storage-layout risk.
- **Frontend `npm audit`**: 0 vulnerabilities (was 14). TypeScript SDK
  `npm audit`: 0 (already clean). Root npm project (semantic-release
  tooling): 19 findings, all locked behind a genuine major-version bump,
  deliberately deferred — see §9.

Everything above was actually re-run after the LAST code change in each
area, not once at the start — several of the "done" phases above
surfaced real regressions from later phases' changes (documented in each
section: the malformed-Authorization-header bug and the CSRF-on-login
exemption gap from Phase 6, the `import json`/`import pytest` unused-
import catches from Phase 9's newly-blocking lint gate, the `any`-typed
mock and CSRF-header omission caught by Phase 10's own frontend tests) —
finding those was the actual point of re-running everything at the end
rather than trusting each phase's own earlier, narrower verification.

**Honesty note on scope:** the spec itself frames this as multi-week
work for a two-person team. This session executes P0 items (2-5) to
completion with real verification, and makes real, working progress on
P1/P2 (6-10) rather than attempting a shallow pass across all eleven
phases. The final report will say exactly what's done, what's partial,
and what's untouched — no phase gets marked complete without the
verification command that proves it.

## §3 — Branch protection, CODEOWNERS, release pipeline

**CODEOWNERS**: created at `.github/CODEOWNERS`, scoped to backend
auth/tenancy, contracts, CI/deploy config, and frontend auth — both
maintainers listed on every path (a single-owner path can't be approved by
its own author on a two-person team).

**Real bug found and fixed**: `.github/workflows/test.yml` already pins
`foundry-rs/foundry-toolchain@v1` to `v1.8.1` (a teammate's fix, committed
2026-09-07, for the same "unpinned stable drifted and broke the gas
snapshot" root cause) — but `deploy.yml` (2 occurrences) and `k6.yml` (1
occurrence) were still pinned to the *old* `1.7.1` fix, with a comment
claiming to "match test.yml's own pin", which was no longer true. Since
`release.yml` only runs when `test.yml` ("CI") succeeds on `main`, and
`deploy.yml` runs after `release.yml`, a real deploy could have run against
a different Foundry version than the one CI actually validated. Fixed:
bumped both to `v1.8.1`, corrected the now-accurate comments. Verified for
real, not just read: installed forge 1.8.1 locally via `foundryup --install
1.8.1` and reproduced the committed `.gas-snapshot` cleanly (`forge
snapshot --check` clean, `forge test` 147/147 — matching what
`foundry.toml`'s `isolate`/`dynamic_test_linking` comment already claims).

**Release workflow itself**: ran `npx semantic-release --dry-run --no-ci`
locally — every plugin (`commit-analyzer`, `release-notes-generator`,
`changelog`, `git`, `github`) loads cleanly, it correctly no-ops on a
non-main branch. No other bug found in `release.yml`/`.releaserc.json`
beyond the Foundry pin drift above. **Could not fetch the actual failed CI
run's logs** — no `gh` CLI, no token, and this sandbox's shared IP is
already rate-limited on the unauthenticated GitHub API. If CI is still red
after this fix, the next step is pulling the real run's logs with a
token/`gh auth login`, not guessing further.

**Branch protection — cannot be applied from this session** (no `gh`
auth, and this is a repo-admin Settings action this session shouldn't
take unilaterally regardless). Manual checklist, Settings → Branches → Add
rule for `main`:
- [ ] Require a pull request before merging, ≥1 approving review
- [ ] Dismiss stale approvals on new commits
- [ ] Require review from Code Owners (activates the CODEOWNERS file above)
- [ ] Require status checks to pass before merging, and require branches
      to be up to date — select these real job names from `test.yml`
      ("CI"): **Foundry project**, **Backend tests (3.11)**, **Backend
      tests (3.12)** (or whatever the actual matrix job names resolve to —
      confirm in the Actions tab, since GitHub renders matrix jobs with a
      suffix this file's `name:` field doesn't show verbatim), **Frontend**,
      **SDK integration**, **API compatibility**, and the secret-scanning
      (gitleaks) job
- [ ] Block force pushes
- [ ] Block branch deletion
- [ ] Decide explicitly whether "Do not allow bypassing the above settings"
      (enforce for admins too) is on — the spec's default recommendation —
      or whether an emergency-bypass policy is preferred; this is a real
      tradeoff for a two-person team (an admin locked out of their own
      emergency fix vs. an admin able to silently skip review), not
      something to default silently

## §4 — Infra isolation

- `docker-compose.yml`: every one of its 10 host port publishes
  (postgres/anvil/redis/mcp-search/mcp-blockchain/api/anchor-worker/
  indexer/integrity-watchdog/alloy) now binds to `127.0.0.1` explicitly,
  confirmed via `docker compose config --quiet` (default and
  `--profile observability`) both still validating clean. Local dev is
  unaffected (`localhost:PORT` still works) — only reachability from
  outside the host changes.
- `docker-compose.production.yml` (new): separate topology, no `anvil`
  service, every secret required via `${VAR:?message}` (confirmed:
  `docker compose -f docker-compose.production.yml config --quiet` fails
  loudly listing the missing var when secrets aren't set, succeeds with
  them set — tested both directions for real). Only `caddy` publishes a
  host port (80/443).
- `deploy/production/Caddyfile` (new): reverse-proxies to `api:8000`,
  returns 404 for `/metrics` at the edge (Alloy still reaches it directly
  over the internal network — never needs the public path).
- `backend/.env.production.example` (new): every required production
  variable, empty values only, gitignore updated so this specific
  `.example` file (unlike `.env.production` itself) is trackable.
- `GET /chain-status`: no longer returns the raw `rpcUrl` (a real
  deployment's provider URL can embed an API key in the path) — returns
  `rpcHost` (hostname only) instead. Also stopped leaking the raw
  exception string in the disconnected-state response body (same rule
  `/ready` already followed) — logs it server-side, returns a generic
  message. Frontend's `ChainStatus` type updated; confirmed `rpcUrl` was
  never actually rendered anywhere in the UI, so this is a zero-behavior-
  change rename from the frontend's perspective.
- `GET /metrics`: optional second layer — `METRICS_AUTH_TOKEN` (empty by
  default, matching today's behavior exactly) requires a matching
  `X-Metrics-Token` header when set. Primary protection is still network
  isolation (production publishes no metrics ports, Caddy 404s the public
  path) — this is defense-in-depth, not a replacement for it.
- **Real, separate bug found while verifying this phase**: the
  `trustchain_api` Postgres role's password is hardcoded directly in the
  original RLS migration (`9f3a1c7d5e2b`) — `ALTER ROLE ... PASSWORD
  'trustchain_api_dev_password'`, not parameterized. Every deployment that
  has ever run `alembic upgrade head`, including a real production one,
  gets the identical checked-in-to-this-public-repo password for the one
  role Row-Level Security binds to. Fixed with a new migration
  (`e897e74cf66b`, not an edit to the historical one) that ALTERs the role
  from `POSTGRES_API_PASSWORD` when set, defaulting to the original dev
  literal when it isn't — verified for real against the live local
  Postgres: upgraded, downgraded, re-upgraded, confirmed the role still
  authenticates with the dev password throughout (zero behavior change for
  local dev/CI), plus a standalone check that the SQL-quoting helper
  correctly escapes an embedded `'`.

## §5 — Signed stream token

`GET /stream/{run_id}` used to accept nothing but a guessable run_id.
Implemented exactly per spec:

- `auth.create_stream_token`/`decode_stream_token` (`backend/auth.py`): a
  distinct JWT audience (`trustchain-stream`) from the main session token,
  hard 5-minute expiry, unique `jti`.
- `POST /run-agent` mints one and returns it embedded in `stream_url`
  (`/stream/{run_id}?token=...`) — this is now the *only* way to get a
  working stream URL.
- `GET /stream/{run_id}` validates the token, checks the path's `run_id`
  matches the token's, and cross-checks the token's `project_id` against
  the run's *actual* project via `db.get_run` (not just trusting the
  token's own claim) — rejects with 401 (missing/invalid/expired) or 403
  (wrong run / wrong project).
- `POST /runs/{run_id}/stream-token` (new): mints a replacement token for
  reconnect, scoped to the caller's own project (404s, not 403, for
  another project's run — same information-hiding as `GET /runs/{run_id}`).
- Access-log token leak (a real, empirically-confirmed issue, not
  hypothetical): uvicorn's default access logger writes the full request
  line including query string — confirmed via a live repro
  (`"GET /chain-status?token=SUPERSECRETTOKENVALUE12345 HTTP/1.1" 200 OK`
  in stdout). Fixed with a `logging.Filter` on the `uvicorn.access` logger
  (`backend/logging_config.py`) that redacts `token=`/`api_key=` query
  params in place — reran the identical repro after the fix and confirmed
  `token=***REDACTED***` with the raw value nowhere in the log.
- `Referrer-Policy: no-referrer` added globally (new middleware in
  `main.py`) so a page embedding any of this API's URLs can't leak one via
  the `Referer` header on navigation.
- Frontend (`frontend/hooks/useAgentStream.ts`, `frontend/lib/api.ts`):
  now opens `EventSource` using *only* the backend-returned `stream_url` —
  the old `streamUrl(runId)` helper that built an unauthenticated URL
  itself is deleted (replaced by `resolveStreamUrl`, which just prefixes
  the API host onto a relative URL the backend already gave it). Added a
  proactive refresh at 4.5 minutes (`refreshStreamToken` →
  `POST /runs/{run_id}/stream-token`) that reopens the connection with a
  fresh token before the original 5-minute one expires — chosen over
  reactive refresh-on-401 because `EventSource.onerror` doesn't expose the
  HTTP status code a browser actually received, so there's no reliable way
  to distinguish "token expired" from "network blip" after the fact.
- **Real backward-compatibility bug found and fixed**: both SDKs and the
  TypeScript CLI built `/stream/{run_id}` URLs directly from a bare
  `run_id`, with no token — this endpoint change would have silently
  broken every one of them. Fixed: TypeScript SDK's `stream()` now takes
  `(runId, streamUrl, timeoutMs)` (a breaking signature change, judged
  acceptable since this SDK "is not yet actually published anywhere" per
  CLAUDE.md); Python SDK's `stream()` takes an *optional* `stream_url` and
  auto-mints one via a new `get_stream_token()` when omitted, so
  `client.stream(run_id)` alone still works unchanged — genuinely
  backward compatible, not just less-broken. Both `run_and_wait`
  variants updated to pass the real `stream_url` through.
- `docs/phase5-frontend-contract.md`'s route table corrected (was still
  documenting this as "none... deliberately unauthenticated").

**Verified for real, end to end, against a live backend + real
Postgres/Redis** (not mocked): valid token → stream opens; missing token →
401; token minted for a different run_id → 403; tampered/gibberish token →
401; a second project's token/reconnect attempt against the first
project's run → 404; `POST /runs/{run_id}/stream-token` mints a working
replacement that the stream endpoint then accepts. Ran the same suite of
checks a second time through the actual Python SDK (not raw curl) —
explicit-`stream_url` path, the backward-compat auto-mint path, and
`run_and_wait` all passed. Frontend: `tsc --noEmit`/`eslint`/`next build`
all clean (same 3 pre-existing warnings, 0 errors). TypeScript SDK:
`tsc -p tsconfig.json` clean.

**Tests added** (`backend/tests/test_sse.py`) after the manual repros
above confirmed the design: 8 new tests (missing token, run that doesn't
exist, token minted for a different run, token scoped to another project,
expired token, reconnect endpoint success, reconnect endpoint rejecting
another project, plus a Redis-silence-timeout test rewritten to use a
directly-DB-created run rather than a nonexistent one — see that test's
own docstring for why the old "unknown run_id" premise no longer applies
once a stream token requires the run to actually exist). Also fixed 2
pre-existing tests in the same file that called the stream endpoint with
no token at all and would otherwise have started failing the moment this
shipped — found by actually running the suite, not by inspection.
11/11 passing, plus the surrounding auth/main/permissions/multi-tenancy
suites (104 tests total) re-run clean to confirm no wider regression.

## §6 — Cookie-based browser auth + CSRF

Built on top of `refresh.py`'s already-existing rotating-refresh-token
mechanism (issue_token_pair/rotate_refresh_token, with reuse detection)
rather than inventing a new session mechanism — that code already existed,
additive, unused by anything until now.

- **Cookies**: `POST /auth/signup`/`/auth/login` keep their exact JSON body
  (`{token, name, email}` — required for `trustchain-cli`'s `login`
  command, which parses `body["token"]` directly) but ALSO set three
  cookies: `tc_access` (HttpOnly, 15 min), `tc_refresh` (HttpOnly, 30
  days), `tc_csrf` (NOT HttpOnly — the frontend has to read it). Domain/
  Secure/SameSite all come from new `config.py` settings
  (`COOKIE_DOMAIN`/`COOKIE_SECURE`/`COOKIE_SAMESITE`), no hardcoded
  domain anywhere.
- **Dual auth resolution**: `auth.get_current_user`/`get_current_principal`
  now accept EITHER the `Authorization: Bearer` header (unchanged) OR the
  `tc_access` cookie, header taking precedence. A malformed Authorization
  header (present but not "Bearer "-prefixed) is rejected outright rather
  than silently falling back to a cookie — see the real bug this
  distinction fixes, below.
- **CSRF**: double-submit design — `_csrf_protection_middleware` in
  `main.py` requires a matching `X-CSRF-Token` header on every unsafe
  (POST/PUT/PATCH/DELETE) request that's authenticated via the `tc_access`
  cookie specifically (not Bearer/API-key — those can't be forged by a
  third-party page the way an auto-attached cookie can). `/auth/login` and
  `/auth/signup` are deliberately exempt — see the exemption's own code
  comment for the full reasoning; the short version is that they establish
  a session rather than act on one, and `tc_csrf` doesn't even exist until
  after a first successful login/signup, so requiring it there is either
  meaningless or actively breaks re-login flows for no real security gain
  in this app's threat model.
- **Refresh/logout**: both now accept the refresh token from EITHER the
  JSON body (SDK/CLI, unchanged) OR the `tc_refresh` cookie (browser, body
  omitted) — body wins if both present. Logout clears all three cookies
  AND revokes the refresh-token family server-side.
- **`POST /auth/switch-project`**: also re-mints cookies (fresh
  issue_token_pair) when the caller has a `tc_access` cookie, so a
  cookie-authenticated browser session actually picks up the new
  project scope. Known simplification: this starts a NEW refresh-token
  family rather than rotating/revoking the old one, so a switch leaves
  the pre-switch family valid-but-unused until its own 30-day expiry —
  low risk (both families belong to the same legitimate user), not
  revoked in this pass.
- **Frontend** (`frontend/lib/auth.ts`, `frontend/lib/api.ts`): no more
  token in localStorage — `lib/auth.ts` now holds only `{name, email}` for
  the navbar display, plus a `csrfHeader()` reader. Every API call in
  `lib/api.ts` now routes through one `apiFetch` helper: always
  `credentials:"include"`, CSRF header auto-attached on unsafe methods,
  and exactly one silent `POST /auth/refresh` + retry on a 401 (skipped
  for the auth endpoints themselves, to avoid recursion/nonsense retries).
  `ClientShell.tsx`'s logout now calls the real backend logout (revokes
  the family) before clearing local state, not just a local clear.

**Two real bugs found and fixed while verifying this, both via actually
running the test suite, not by inspection:**
1. The malformed-Authorization-header case above —
  `test_auth.py::test_run_agent_rejects_missing_bearer_prefix` started
  returning 200 instead of 401, because the naive cookie-fallback logic
  treated "header present but wrong format" the same as "no header at
  all." A signed-in browser sending a badly-formed Authorization header
  would have silently succeeded via its own valid cookie instead of being
  rejected — fixed by distinguishing "no header" from "bad header."
2. The CSRF middleware initially had NO exemption for login/signup,
  which broke 5 pre-existing tests that do signup-then-login (or
  signup-twice) on the same client — the second call carried the first
  call's leftover `tc_access` cookie and got CSRF-blocked before ever
  reaching the real business-logic check (wrong password, duplicate
  email). This is exactly the login-flow breakage the spec explicitly
  warned against causing.

Also fixed one pre-existing test (`test_main.py::
test_error_responses_carry_a_stable_machine_readable_error_code`) whose
final "assert unauthenticated" step now needs `client.cookies.clear()`
first — a leftover valid session cookie from an earlier step in that same
test now legitimately authenticates a call the test intended to be
credential-free, which is correct new behavior, not a bug to work around.

**A full whole-suite run (not just the auth-adjacent files) surfaced 3
more, same family**: `POST /auth/verify-email/{token}` and `POST
/auth/reset-password/{token}` are both "Unauthenticated by design" per
their own existing docstrings — gated entirely by possession of an
emailed, single-use token in the URL path, explicitly meant to work from
wherever the recipient opens the email, not tied to any particular
browser session. Both got the same false-CSRF-block as login/signup for
the same reason (a leftover `tc_access` cookie from the test's own
preceding signup call) — extended the exemption from a literal path set
to also cover these two by prefix (they carry a variable token in the
path). The third: `test_v1_and_new_endpoints.py::
test_v1_runs_stream_is_an_alias_for_stream` — a Phase-5-era gap this
session's earlier `/stream/` grep sweep missed (it searched for the
literal substring `/stream/` with a trailing slash; this test's URL,
`/v1/runs/{id}/stream`, ends in `/stream` with no trailing slash, so it
silently didn't match). Same fix pattern as test_sse.py's Phase 5 tests:
rewritten to mint a real stream token and assert the alias 403s
identically to the base route for a nonexistent run — arguably a more
meaningful proof of "genuinely the same handler" than the original
timeout-based version. Removed an `import json` this same edit made
newly-unused (ruff caught it) rather than leaving the file lint-broken.

Full backend suite: first full run (before any of the 3 fixes above)
was 393 passed / 3 failed / 3 skipped. A targeted re-run of every touched
file after fixing all 3 came back 96/96 clean. A second full-suite run
confirmed the whole thing end to end: **396 passed, 3 skipped
(pre-existing, unrelated), 0 failed** (`pytest -q`,
`TESTCONTAINERS_RYUK_DISABLED=true`, 457s).

**Verified for real**: full backend cookie/CSRF flow exercised twice —
once via raw `curl` with a cookie jar against a live backend + real
Postgres/Redis (signup → cookie-only GET → CSRF block → CSRF pass →
refresh → logout, plus a Bearer-only request proving CSRF exemption), and
again as 10 new automated tests (`tests/test_cookie_auth.py`) covering
cookie attributes, CSRF accept/reject, Bearer exemption, refresh rotation
(both cookie- and body-driven), logout clearing + family revocation, and
an expired-cookie → refresh → recovery sequence. Full backend suite
re-run clean after both bug fixes (see exact count in the final report).
`tsc --noEmit`/`eslint`/`next build` all clean on the frontend.

**Not verified**: a real browser. Chrome automation wasn't connected in
this environment (`tabs_context_mcp` returned "Browser extension is not
connected"), so the actual click-through — sign up in a real browser tab,
confirm `localStorage` has no token, reload and confirm the session
survives, log out and confirm redirect — was not performed. Everything
above is verified at the HTTP-contract level (which is what the frontend
code actually depends on), not at the rendered-UI level. Flagging this
explicitly rather than claiming a UI test that didn't happen.

## §7 — EvidencePublisher

New `evidence/` package, mirroring `notifications/backends/`'s and
`blockchain/signer.py`'s existing "one Protocol, one factory keyed off a
config string" shape exactly (ADR-0008):

- `evidence/manifest.py::build_manifest` — deterministic, no-PII-by-
  construction (its signature only ever accepts hashes/counts/ids from
  `anchor_worker/batch.py::build_batches`'s output — there is no
  parameter it could be handed that would let raw prompt/output/email/
  token content into the published manifest). Schema:
  `{schemaVersion, runIdHash, merkleRoot, stepCount, leafOrder,
  leafHashes}`.
- `evidence/backends/` — `disabled` (default, always raises
  `EvidencePublishError`, never fabricates a URI) and `pinata` (real IPFS
  pinning via Pinata's REST API, one HTTPS POST + a JWT — same "no
  infrastructure this project has to run itself" reasoning as
  `notifications/backends/brevo.py`'s choice over raw SMTP).
- **Wired into `anchor_worker/main.py`**: a new `publish_evidence()` runs
  BEFORE `submit_batch()` for every batch — builds the manifest, attempts
  to publish, and (only on success) persists the resulting URI to a new
  `anchor_batches.evidence_cid` column (migration `9e7462593d21`)
  *before* the on-chain `anchorBatch()` call, so a crash between
  "published" and "anchored" doesn't lose track of where the manifest
  landed. Never raises — a disabled backend or a real publish failure
  just means `meta_uri=""` (NULL `evidence_cid`), and anchoring proceeds
  exactly as it always has. `submit_batch()`'s hardcoded `meta_uri = ""`
  became a real parameter it's told, not something it decides.
- **Idempotency, scoped honestly**: IPFS content-addressing means
  re-publishing an identical manifest lands at the identical CID for
  free — the manifest's own determinism is what actually buys "retrying
  this batch's evidence publish is safe," not extra dedup logic on top.
  What this pass does NOT solve: a worker crash that leaves an
  `anchor_batches` row stuck at `status='building'` forever (nothing
  currently re-scans for those) is a pre-existing gap in the batch
  lifecycle generally, not something introduced by or specific to
  evidence publishing — out of scope for this specific wiring.
- **Overclaiming-language audit** (README/landing copy/docs/SDK docs/
  dashboard labels, per the spec): swept the whole repo for "permanently
  recorded"/"immutable"/"tamper-proof"/"every step...on-chain"-style
  phrasing. Most of it was already precise — this codebase's own
  documentation culture (CLAUDE.md) already enforces "why, not just
  what," and it shows: `docs/threat-model.md`, `docs/architecture.md`,
  and the real rendered marketing copy (`frontend/components/marketing/
  content.ts`) all already say things like "anchors only the resulting
  root on-chain" and "each step still gets its own cryptographic proof."
  Found exactly one genuine offender: `frontend/app/auth/page.tsx`'s
  "Every step on-chain" badge — fixed to "Merkle-anchored on-chain."
  README.md itself still overclaims ("Every Decision... Permanently
  Recorded On-Chain") but Phase 8 replaces it wholesale, so fixing its
  wording here would be wasted effort — flagged, not silently skipped.

**Verified for real**: 24 tests, all passing — manifest determinism,
manifest-changes-if-tampered, the no-PII field whitelist, a genuine
Merkle-proof reconstruction and verification using ONLY the manifest's
`leafHashes`/`leafOrder` plus a root (no Postgres access, proving the
actual point of publishing this at all), the disabled backend always
raising, the Pinata backend against a REAL local HTTP server (not a
mock — same `ThreadingHTTPServer`-on-loopback pattern
`test_email_delivery.py`'s Brevo test already established), a real
Pinata-rejection error surfacing verbatim, and `publish_evidence()`
persisting `evidence_cid` against a real Postgres. Existing
`test_anchor_worker.py` (13 tests, including one that anchors a real
batch on real Anvil and verifies the proof on-chain) re-run clean —
`submit_batch()`'s new `meta_uri` parameter defaults to `""`, zero
behavior change for the untouched default path.

## §8 — README replacement

`README.md` is now exactly the spec's own minimal structure — centered
logo, centered `TrustChain` heading, animated Coming Soon GIF, no other
prose, alt text on both images:

```html
<div align="center">
  <img src="./frontend/public/logo_transparent.png" alt="TrustChain logo" width="150" />
  <h1>TrustChain</h1>
  <img src="./assets/readme/trustchain-coming-soon.gif" alt="TrustChain — Coming Soon" width="520" />
</div>
```

Every architecture diagram, badge, tech-stack table, quick-start
instructions, contract-address table, and roadmap that used to be here is
gone — not trimmed, deleted, matching the spec's explicit instruction.

**The GIF asset is real, not a placeholder** — this session has no
AI image/video generation tool, so it was built the only other honest
way available: a small, disposable Python script (Pillow, not committed
to the repo — it was scratch tooling, not a product asset) rendering 36
frames by hand — dark background, a 7-node chain with a cyan→purple
glowing pulse that sweeps across it, "TRUSTCHAIN" kicker text, and a
"COMING SOON" title with its own slower glow pulse — reusing this
project's own existing brand colors (`#00D9FF` cyan, `#836EF9` purple,
both pulled from the OLD README's own badge/typing-SVG color codes, so
the new asset is still visually consistent with the brand rather than
inventing new colors). 201 KB, 520×200, saved at
`assets/readme/trustchain-coming-soon.gif`. Inspected two extracted
frames directly (not just trusted the generation code) before finalizing
— confirmed both the cyan and purple ends of the gradient render as
intended and the text is legible.

Also closed a loose end from Phase 2's own spec, missed at the time: "add
a clearly scoped future 3D landing-page backlog item rather than
retaining dormant code" — created `docs/backlog.md` with that entry
(what was removed, why, and what a future re-implementation should do
differently rather than resurrecting the deleted tree wholesale).

**Not verified**: an actual GitHub render. The HTML (`<div align="center">`,
raw `<img>`/`<h1>` tags) is a standard, extremely common GitHub-Flavored-
Markdown pattern, and both referenced files were confirmed to exist at
the exact relative paths the markdown points to (`file` confirms real
PNG/GIF data, not corrupt/empty files) — but this session can't push, so
"does GitHub's own renderer actually display it correctly" wasn't
checked against the real github.com UI. Worth a quick manual look once
this branch is reviewed/pushed.

## §9 — CI gates, dependency upgrades, release runbook

**Release runbook**: `docs/release-process.md` already covers everything
the spec asks for (conventional commit rules, a real verified dry-run,
artifact/image verification via cosign, the rollback boundary via
`canary_rollout.sh`'s automatic bake-or-rollback, and exactly which
secrets a real deploy target needs) — no rewrite needed, just confirmed
it's current against Phase 3's earlier verified `semantic-release
--dry-run` run.

**`continue-on-error` inventory** — 6 real entries in `test.yml` (a 7th
grep hit was a comment mentioning the phrase, not a directive):

| Gate | Before | After |
|---|---|---|
| `ruff format --check` | non-blocking (52 files, per a stale comment — actually 117 now) | **Blocking**, against a committed baseline (`backend/.ruff-format-baseline.txt`, `backend/scripts/check_ruff_format_baseline.py`) — see below |
| `mypy` | non-blocking ("40 findings" — actually 103 raw / 82 unique now) | **Blocking**, same baseline mechanism (`backend/.mypy-baseline.txt`, `backend/scripts/check_mypy_baseline.py`) |
| Frontend `npm run lint` | non-blocking | **Blocking** — verified locally the underlying command already exits 0 on warnings-only; the gate was never actually catching anything |
| Frontend `npm audit` | non-blocking (11-14 findings) | **Blocking** — real fix, see below, now 0 vulnerabilities |
| Schemathesis (API fuzzing) | non-blocking | Left as-is — already the product of 3 real, separately-investigated CI-only flakes not reproducible locally; nothing this session found improves on that |
| Trivy (image scan) | non-blocking | Left as-is — real HIGH/CRITICAL findings in the base image + transitive deps, not fixable by a code/config change alone |

**Why a baseline instead of a mass reformat/fix**: `ruff format` would
touch 117 files, `mypy`'s 82 findings span 33 files — fixing either
outright in this pass would (a) bury this session's actual functional
changes in a huge mechanical diff, exactly what CLAUDE.md's own
"non-blocking gates are deliberate" note warns against forcing as a side
effect, and (b) cost more verification time than remained sensible to
spend on formatting/typing debt that predates this entire hardening
pass. The baseline scripts (modeled on each other, same shape) make the
gate **actually block new problems today** — verified both directions
for each: passes clean against the current baseline, fails loudly and
specifically when a genuinely new violation is introduced (tested with
a deliberately bad throwaway file in both cases). A real bug this
caught immediately: `tests/test_cookie_auth.py` (written earlier this
session, Phase 6) had a genuinely unused `import pytest` — ruff check
(not format — a separate, already-blocking gate) caught it the moment
this section's local verification pass actually ran it.

**Frontend `npm audit` — real fix, not just re-scoped**: `npm audit fix`
(safe) plus `npm audit fix --force` for the rest resolved all 14 current
findings (grown from the comment's stale "11") to **0 vulnerabilities**.
The remaining 3 (next/postcss/sharp, all transitively via Next.js) turned
out to only need Next.js `16.2.7 -> 16.3.4` — confirmed via npm's own
audit data (`isSemVerMajor: false`) to be a **minor** version bump, not
the major-version risk the old comment assumed (that assumption likely
came from the exact-pin `"16.2.7"` in `package.json` making npm describe
any bump at all as "outside the stated dependency range," regardless of
semver severity). Verified for real after: `tsc --noEmit`/`eslint`/
`next build` all clean, all 11 routes still generate, `npm audit` reports
0 vulnerabilities.

**Two dependency backlogs deliberately left alone, both genuinely major**
(the spec's own "keep breaking upgrades isolated... do not merge a
dependency bump that breaks CI just to reduce the backlog count," applied
literally): the TypeScript SDK's own `npm audit` is already clean (0
findings, checked directly). The repo-root `npm audit` (semantic-release
tooling) has 19 findings, but `npm audit fix` (safe, non-forcing) made
**zero changes** — every one of them is transitively locked behind
`semantic-release 24.x -> 25.0.9`, a real major bump with no partial safe
fix available. Not attempted this session — it would need the same
`--dry-run` re-verification Phase 3 already did once, and this session's
remaining budget didn't cover doing that properly. Flagged, not silently
skipped.

**Dependabot grouping** (`.github/dependabot.yml`): every one of the 13
ecosystem/directory blocks now groups its own minor+patch updates into
one weekly PR — this, not team discipline, is the actual fix for "dozens
of stale PRs": 13 blocks × however many packages update in a typical
week, one PR each, was structurally guaranteed to pile up regardless of
how promptly anyone reviewed them. Majors stay ungrouped and individually
reviewable on purpose (see the file's own comment — the Next.js example
above is exactly why that distinction has to survive: an upgrade that
LOOKS major from a pin string but isn't needs to still be individually
inspectable, not auto-bundled with unrelated patches). GitHub Actions
updates are deliberately NOT grouped either, for a sharper reason: an
Actions version drifting silently is the literal root cause of this
session's own Foundry-pin incident (§3) — those specifically want MORE
individual scrutiny, not less.

**Verified for real**: the exact updated blocking-gate commands re-run
locally in sequence (`ruff check .` — caught and fixed the real unused-
import bug above; both baseline scripts; `bandit`) all clean. Full
backend suite re-run after every change in this section — see the final
report for the exact count.

## §10 — Revocation semantics, tests, and the refactor NOT attempted

**Revocation-semantics decision (made explicitly, not defaulted
silently)**: read the deployed `AgentIdentityRegistryV2.sol` directly
rather than guessing. `registerAgent()`'s already-registered branch
(`isRegistered[key] == true`, which `revokeAgent()` never clears) updates
`codeHash`/`modelName`/`modelVersion` but never touches `isActive` —
there is no code path anywhere in the deployed contract that sets
`isActive` back to `true` once revoked. **Revocation is permanent by
construction on the contract that's actually live.** Chose "correct the
claim" over "add a reactivation path," per the spec's own "do not
silently change deployed contract behaviour" — a new reactivation
function would mean a new contract, a real deploy, and its own audit
trail, none of which this session was asked to do. Found the actual
incorrect claim (not a hypothetical): `main.py`'s `GET /agents` docstring
said "a revoked agent_id can be re-registered fresh" — false, fixed to
document the real behavior precisely (and why: `isActive` staying false
regardless of re-registration, `verifyAgent()` keeping returning `false`
for it). Swept the rest of the repo for the same claim — found nowhere
else.

**Regression test** (`tests/test_v1_and_new_endpoints.py::
test_reregistering_a_revoked_agent_does_not_reactivate_it`): register →
revoke → re-register with a genuinely different hash → hash updates
(proving re-registration itself still works) but `isActive` stays
`false` and `isValid` stays `false`. Run against **real Anvil**, not
asserted from reading the Solidity — passed on the first real run,
confirming the analysis rather than just asserting the fix's own
premise.

**Frontend tests — genuinely new territory, this frontend had zero
tests before this session:**
- **Vitest unit tests** (`lib/auth.test.ts`, `lib/api.test.ts`, 17
  tests): the session-display/CSRF-cookie helpers (never storing a
  token, decoding the double-submit cookie correctly) and `apiFetch`'s
  actual contract (credentials always included, CSRF header only on
  unsafe methods, exactly one silent refresh-and-retry on 401, no
  infinite loop if the retry also fails, the stream-token reconnect
  path). Hit a real Node 25 / jsdom conflict immediately (Node's now-
  native `localStorage` shadowing jsdom's, missing `.clear()`) — fixed
  with `NODE_OPTIONS=--no-experimental-webstorage`, baked into the
  `npm run test` script so nobody has to rediscover it. Also caught two
  bugs in the tests themselves before they'd have been meaningless
  (a URL-matching bug from not accounting for `getRuns()`'s `?limit=50`
  query string, and an `any`-typed mock parameter that would have failed
  the newly-blocking lint gate in CI).
- **Playwright smoke suite** (`e2e/smoke.spec.ts`, 4 tests) — genuinely
  run in a real headless Chromium against a real backend + real Postgres/
  Redis, not skipped or stubbed, despite `tabs_context_mcp` reporting the
  Chrome extension unavailable earlier this session: Playwright manages
  its own browser binary independently of that extension. The sandbox's
  OS isn't officially supported by Playwright (a real warning, not
  ignored) but the browser download and launch both worked once
  installed without `--with-deps` (which needs sudo this sandbox
  doesn't have interactively — CI's `ubuntu-latest` runners do, and
  the CI step added to `sdk-integration` uses `--with-deps`). Covers:
  the public landing page, an unauthenticated `/dashboard` visit
  redirecting to `/auth`, a full real signup → dashboard → **verified
  in a real browser that `localStorage` never holds a token, and that
  the real `tc_access` cookie is `HttpOnly`** → logout, and starting a
  real run (`POST /run-agent`) through the browser's own cookies →
  authorised stream succeeds, unauthorised (no token) stream request
  gets 401. Caught a real bug in the test itself on first run — calling
  the raw API via `page.request` bypasses `apiFetch`'s automatic CSRF
  header, so the first attempt correctly got rejected by the CSRF
  middleware; fixed by reading the `tc_csrf` cookie and attaching the
  header by hand, same as the real frontend does. Wired into CI as a
  new step in the `sdk-integration` job (reuses the real backend stack
  already brought up there, same reasoning as that job's existing k6
  smoke step) rather than standing up a second full stack.

**The router/module split — deliberately NOT attempted.** `main.py` is
now 2,717 lines (grew further this session: CSRF middleware, cookie
routes, the stream-token endpoint and reconnect route, evidence-related
wiring), `db/models.py` 783, `blockchain/client.py` 854. Splitting
`main.py` into the spec's proposed router groups (auth/session, runs/
streams, agents/steps/proofs, orgs/memberships/invitations, alerts/
integrity, public/status) is real, valuable work — but it's also
genuinely the highest-risk item left: a mistake in extracting routes
(a missed `/v1` dual-mount, a broken `Depends()` chain, a subtly
different error shape) is exactly the kind of thing that's easy to miss
by reading the diff and only shows up when a specific route is actually
hit. Doing it NOW, layered on top of this session's own extensive
functional changes to that exact file (auth routes, the CSRF middleware,
the stream endpoint, evidence wiring, the revocation docstring), with no
fresh reserve of session budget to absorb a full re-verification cycle
if something broke, is precisely the "speculative rewrite" risk the
spec's own P2 preamble warns against — "refactor only after the security
behaviour is covered by tests" is satisfied now (it wasn't at the start
of this session), but "bounded" still means something. Flagged as the
single largest remaining item for a dedicated future session with its
own full reserve of verification time, not silently dropped.

**Verified for real**: the Anvil-backed revocation test passed on a real
chain. Frontend: `tsc`/`eslint`/`vitest`/`next build` all clean, and the
Playwright suite passed 4/4 against a genuinely live backend + browser
(with one real bug caught and fixed in the test itself along the way).
Backend: see the final report for the exact full-suite count after this
section's changes.

## Notes on specific decisions

- **Revocation semantics (Phase 10):** `AgentIdentityRegistryV2` already
  keeps `isActive=false` permanently after revocation — this is
  *existing deployed contract behavior*, not something this pass is
  choosing. Where docs/copy claim re-registration is possible, they'll
  be corrected to match the deployed contract rather than the contract
  being changed — no redeploy, no storage-layout risk, consistent with
  the "do not silently change deployed contract behaviour" rule.
- **README GIF asset (Phase 8):** this session has no image/video
  generation tool, so a literal glowing cyan/purple animated GIF can't
  be produced. The README will be restructured per spec with a
  documented placeholder and instructions for dropping in a real asset,
  flagged explicitly rather than faked.
- **Cookie-auth rewrite (Phase 6):** touches `backend/auth.py`,
  `frontend/lib/auth.ts`, every frontend fetch call, and must preserve
  SDK/CLI Bearer-token compatibility. Implemented additively (accept
  either credential, same as `get_current_principal` already does for
  API keys vs. JWT) so existing Bearer clients keep working unmodified.
