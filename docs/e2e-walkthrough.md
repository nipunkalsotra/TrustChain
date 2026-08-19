# End-to-end walkthrough: signup → tamper → forensic proof

This is the full A-to-Z path Phase 4 exists to prove works: a real
person registers, verifies their email, invites a teammate, roles are
actually enforced, a real agent logs real steps through the SDK, someone
tampers with the database directly, the system catches it automatically
and emails the right people naming the actual person responsible, and
the owner can independently confirm what the original content was — all
against a real running backend, with **no frontend involved**. Phase 4
is complete precisely when this document is accurate and this script
passes.

Two ways to run it:

- **`scripts/e2e_demo.py`** — fully automated, re-runnable from a clean
  database, asserts at every stage. This is what to run to verify
  nothing regressed.
- **By hand**, following this document stage by stage with `curl` —
  worth doing once, because reading each real response teaches you more
  than a green script ever will. Every command below is copy-pasteable.

Written for a stranger — someone with no context on this repo beyond
having cloned it. If a stage fails, the "what a failure here means"
note is written for that person, not for someone who already knows the
codebase.

## Prerequisites

```bash
# 1. Backend config — fill in GROQ_API_KEY, TAVILY_API_KEY, JWT_SECRET.
#    PRIVATE_KEY can stay blank (see G7 below); EMAIL_BACKEND=console
#    (the default) is what this walkthrough assumes throughout.
cp backend/.env.example backend/.env

# 2. Start everything: Postgres, Redis, Anvil, V2 deploy, all 7 services
./start.sh

# 3. Confirm readiness — use /ready, not /health (see "A note on /health" below)
curl -s localhost:8000/ready | jq
```

Expected `/ready` output:
```json
{"ready": true, "checks": {"database": {"ok": true}, "migrations": {"ok": true}, "chain": {"ok": false}}}
```
`"chain": {"ok": false}` here is **expected**, not a failure — it means
`PRIVATE_KEY` isn't set (see below), and `/ready`'s own design treats
chain reachability as informational, never blocking (`main.py`'s own
comment on why). If `"database"` or `"migrations"` is `false`, something
is genuinely wrong — check `.logs/fastapi.log`.

### A note on `/health` (G7)

`/health` exercises the V1 chain bridge specifically, which needs a real
Monad testnet `PRIVATE_KEY` this walkthrough never sets. Since Phase 4,
that shows up as `{"status": "not_configured", "reason": "..."}` (HTTP
200) — **not** a 503. A 503 from `/health` now means something is
actually wrong (RPC unreachable, bad key); a bare "not configured" no
longer looks like a failure. `/ready` (above), not `/health`, is what
this walkthrough — and any real readiness probe — should gate on.

### `scripts/e2e_demo.py`'s one real limitation, stated plainly

`email_verification_tokens`/`password_reset_tokens`/`invitations` store
only a hash of their bearer token — by design (ADR-0014), the same
discipline API keys and refresh tokens get. There is no way to recover a
raw token from the database after the fact. `scripts/e2e_demo.py`
therefore extracts each real token from the **actual queued email's real
text**, by tailing `EMAIL_BACKEND=console`'s real structured log output
(`.logs/fastapi.log` for signup/invite/reset emails, `.logs/
integrity_watchdog.log` for tamper-alert emails — two different
processes, see Stage 7 below). This is not a shortcut around sending
real email; it's reading the real email that really got queued. If your
backend is configured with `EMAIL_BACKEND=brevo` for a genuine real-inbox
run, the script can't read your inbox for you — read the token from your
own email and complete that stage by hand, or run this walkthrough
manually.

## Running it automatically

```bash
pip install -e sdk/python   # once, if not already installed
python3 scripts/e2e_demo.py --base-url http://localhost:8000
```

Real, verified output from an actual passing run (edited only to
shorten long hashes/ids — nothing here is invented):

```
  · Checking http://localhost:8000/ready ...
  ✓ backend is ready (DB reachable, migrations current)

======================================================================
STAGE 1 · Register and verify
======================================================================
  ✓ signed up: Ana Owner <demo+owner+1787147953@example.com>
  ✓ extracted verification token from the real queued email (console backend log)
  ✓ A1+A2 pass: org=11, role=owner, emailVerified=true

======================================================================
STAGE 2 · Invite an admin
======================================================================
  ✓ members now: {'demo+owner+...@example.com': 'owner', 'demo+admin+...@example.com': 'admin'}
  ✓ A3 passes: both members listed with correct roles

======================================================================
STAGE 3 · Prove the roles bite
======================================================================
  ✓ member correctly refused (403) trying to invite
  ✓ A4 passes: admin succeeds, member is refused

======================================================================
STAGE 4 · Connect a real agent through the SDK
======================================================================
  ✓ issued API key ...dd14
  ✓ logged step_id=14 via TrustChain.log_and_wait
  · waiting for the anchor-worker to batch and anchor this step...
  ✓ proof fetched and verifies locally
  ✓ logged a second step via @tc.audited (non-blocking): intent='return_policy'
  ✓ A5 passes: real step_id, proof verifies, step appears via the SDK

======================================================================
STAGE 5 · Read the statistics
======================================================================
  · /stats -> 200
  · /runs -> 200
  · /trust-scores -> 200
  · /leaderboard -> 200
  · /gas-spend -> 200
  · /integrity/status -> 200
  ✓ A6 passes: every statistics endpoint returns data

======================================================================
STAGE 6 · Tamper, attributed to a real person
======================================================================
  ✓ issued individual db credential: trustchain_op_demo1787147953
  ✓ tampered with step 14's output_hash via a raw SQL UPDATE as trustchain_op_demo1787147953
  · waiting for the integrity watchdog's next sweep...
  ✓ A7 passes: alert #9 appeared with no application involvement

======================================================================
STAGE 7 · The email, and establishing what changed
======================================================================
  ✓ alert evidence carries editedByOperator, editedByDbRole, and both hashes
  ✓ A8 passes: both owner and admin were emailed, naming the operator
  ✓ A9 passes: true original confirms, wrong guess is rejected

======================================================================
STAGE 8 · Deletion, and tenant isolation
======================================================================
  ✓ deletion attributed to 'Demo Operator': alert #10
  ✓ A10 passes: second org sees nothing, cross-tenant lookup 404s

======================================================================
ALL STAGES PASSED — run tag 1787147953
======================================================================
```

## Stage by stage, by hand

### Stage 1 — Register and verify

```bash
curl -s -X POST localhost:8000/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ana Owner","email":"you+owner@gmail.com",
       "password":"<a strong unique password>",
       "org_name":"Acme AI","project_name":"Support Bot"}' | jq
# → {"token": "...", "name": "Ana Owner", "email": "..."}
export OWNER=<token from above>
```

Check your inbox (or `.logs/fastapi.log` for `EMAIL_BACKEND=console` —
grep for `email_console_send` and your address) for a "Verify your email
address for TrustChain" message containing a `Verification token: ...`
line, then:

```bash
curl -s -X POST localhost:8000/auth/verify-email/<token-from-email> | jq
# → {"ok": true}
curl -s localhost:8000/me -H "Authorization: Bearer $OWNER" | jq
```

**Passes when:** `/me`'s `user.emailVerified` is `true`, and `active.role`
is `"owner"`.

**A failure here usually means:** a stale/already-used token (the link
is single-use), or `EMAIL_BACKEND` isn't `console`/`brevo`/etc. as
expected — check `backend/.env`.

**Password rejected (400 `password_pwned`)?** That's the real
HaveIBeenPwned k-anonymity check doing its job — the password you chose
is a known breached one. Generate a real one:
`python3 -c "import secrets; print(secrets.token_urlsafe(20))"`.

### Stage 2 — Invite an admin

```bash
ORG=$(curl -s localhost:8000/orgs -H "Authorization: Bearer $OWNER" | jq -r '.orgs[0].id')

curl -s -X POST localhost:8000/orgs/$ORG/invitations \
  -H "Authorization: Bearer $OWNER" -H 'Content-Type: application/json' \
  -d '{"email":"you+admin@gmail.com","role":"admin"}' | jq

# The invitation email's token is the LAST path segment of its "Accept:"
# link (e.g. https://app.trustchain.local/invite/<token>) — the
# invitation template has no "Label: token" line the way verify-
# email/reset-password do.
curl -s -X POST localhost:8000/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"name":"Ben Admin","email":"you+admin@gmail.com",
       "password":"<another strong password>","invite_token":"<from email>"}' | jq

curl -s localhost:8000/orgs/$ORG/members -H "Authorization: Bearer $OWNER" | jq
```

**Passes when:** both members are listed, roles `owner`/`admin`.

One real, non-obvious behavior worth knowing: signing up through a valid
invitation sets `email_verified=true` **immediately** — redeeming a
real, single-use, emailed link is already proof of inbox control, so
there's no separate verify-email step for an invited user, and no
redundant verification email gets sent on top of the invitation one.

### Stage 3 — Prove the roles bite

```bash
# Invite a plain member too, sign them up, then AS THAT MEMBER:
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/orgs/$ORG/invitations \
  -H "Authorization: Bearer $MEMBER" -H 'Content-Type: application/json' \
  -d '{"email":"someone@example.com","role":"viewer"}'
# → must be 403
```

**Passes when:** the member is refused (403 `insufficient_role`), the
admin (repeat with `$ADMIN`) succeeds (200). Test the boundary from the
role that should **fail** — confirming the allowed role works proves
nothing about enforcement actually existing.

### Stage 4 — Connect a real agent through the SDK

```bash
curl -s -X POST localhost:8000/api-keys \
  -H "Authorization: Bearer $OWNER" -H 'Content-Type: application/json' \
  -d '{"scopes":["runs:read","logs:write","agents:register"]}' | jq
```
The scope for `POST /steps` is **`logs:write`**, not `steps:write` — a
name that reads as more obvious than it is; `auth.require_scope`'s
actual call site in `main.py` is the source of truth if this ever drifts.

```python
# examples/support_agent/agent.py — a small, realistic support agent,
# not a placeholder. Run it directly:
export TRUSTCHAIN_API_KEY=tc_live_...
python3 -m examples.support_agent.agent
```
or drive it yourself:
```python
from trustchain_sdk import TrustChain
tc = TrustChain(api_key="tc_live_...", base_url="http://localhost:8000", on_error="raise")
tc.register_agent(agent_id="support-bot", model="llama-3.3-70b", version="2026-08",
                   system_prompt="...")
receipt = tc.log_and_wait(agent_id="support-bot", action="answer_query",
                           input="...", output="...", trust_score=92)
print("step_id:", receipt.step_id)
```

**Passes when:** a real `step_id` returns. Fetching its proof
(`tc.get_proof(receipt.step_id)`) may 404 for the first several seconds —
the anchor-worker **batches** steps rather than anchoring each
instantly (ADR-0002); poll for a bit (`anchor_max_batch_age_seconds`,
default 30s, is `./start.sh`'s own override down to 5s for local dev)
rather than treating an immediate 404 as a failure.

### Stage 5 — Read the statistics

```bash
for p in /stats /runs /leaderboard /gas-spend /integrity/status; do
  echo "── $p"; curl -s "localhost:8000$p" -H "Authorization: Bearer $OWNER" | jq -c
done
# /trust-scores needs a run_id query param — it 422s without one:
curl -s "localhost:8000/trust-scores?run_id=<runId from the proof above>" \
  -H "Authorization: Bearer $OWNER" | jq
```

**Passes when:** every endpoint returns 200 with data reflecting the run
just created.

### Stage 6 — Tamper, attributed to a real person

```bash
cd backend
python3 scripts/db_operator.py create shreshtha --display-name "Shreshtha"
# Prints a password ONCE. Copy it.

PGPASSWORD='<password>' psql -h localhost -U trustchain_op_shreshtha -d trustchain \
  -c "UPDATE steps SET output_hash = '0x' || repeat('f',64) WHERE id = <step_id>;"
```
Wait one watchdog sweep (`watchdog_poll_interval_seconds`, default 60s):
```bash
curl -s localhost:8000/alerts -H "Authorization: Bearer $OWNER" | jq '.alerts[0]'
```

**Passes when:** a `step_row_tampered` alert appears with no application
involvement whatsoever.

### Stage 7 — The email, and establishing what changed

Check both inboxes (or `.logs/integrity_watchdog.log` for
`EMAIL_BACKEND=console` — **not** `.logs/fastapi.log`: the alert-email
sender loop runs *inside* the integrity-watchdog process, a separate
process from the API server, specifically so it shares one delivery
transaction with the detector that raised the alert — see
`integrity_watchdog/main.py`'s own docstring). The mail contains the
operator's real name, which columns changed, and both hashes
(`alert.evidence`'s `editedByOperator`/`editedByDbRole`/`changedColumns`/
`oldOutputHash`/`newOutputHash`).

```bash
curl -s -X POST localhost:8000/integrity/verify-content \
  -H "Authorization: Bearer $OWNER" -H 'Content-Type: application/json' \
  -d '{"stepId":<step_id>,"field":"output",
       "candidateText":"<the text you actually logged in Stage 4>"}' | jq
# → matchesCurrent: false   (the row was altered)
# → matchesOriginal: true   (this WAS the original — now proven)
```

**Passes when:** both inboxes hold the alert naming the operator, the
true original returns `matchesOriginal: true`, and a deliberately wrong
guess returns `false` for both `matchesCurrent`/`matchesOriginal`.

A hash is one-way — no amount of forensics can *recover* text nobody
stored (TrustChain deliberately never keeps raw agent content). What it
CAN do is confirm or refute a candidate the owner already holds.

### Stage 8 — Deletion, and tenant isolation

```bash
PGPASSWORD='<password>' psql -h localhost -U trustchain_op_shreshtha -d trustchain \
  -c "DELETE FROM anchor_outbox WHERE step_id = <step_id>;
      DELETE FROM steps WHERE id = <step_id>;"
```

**Deletion raises a DIFFERENT alert_type than editing** —
`step_missing`, not `step_row_tampered`. A deleted-but-still-anchored
step is caught by the Merkle-root-rebuild detector (`sweep_merkle_roots`,
not `sweep_step_rows`), and its per-step attribution lives under
`alert.evidence.deletionForensics["<stepId>"]`, not at the evidence
top level the edit case uses — the shapes genuinely differ, not just the
alert_type string.

```bash
curl -s localhost:8000/alerts -H "Authorization: Bearer $OWNER" | \
  jq '.alerts[] | select(.alertType == "step_missing")'
```

Then, as a SECOND organization's owner:
```bash
curl -s localhost:8000/runs   -H "Authorization: Bearer $OTHER_ORG" | jq
curl -s localhost:8000/alerts -H "Authorization: Bearer $OTHER_ORG" | jq
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/integrity/verify-content \
  -H "Authorization: Bearer $OTHER_ORG" -H 'Content-Type: application/json' \
  -d '{"stepId":<first org step_id>,"field":"output","candidateText":"anything"}'
# → must be 404, never 403 — a 403 would confirm the step exists
```

**Passes when:** the `step_missing` alert's `deletionForensics` names the
real operator, and the second org sees empty results throughout with a
404 (not 403) on the cross-tenant lookup.

## What this walkthrough found and fixed, for real

Every one of these was a genuine bug or a real inaccuracy in an earlier
draft of this exact plan, found only by actually running it against a
live backend — not by reading code:

- A step deleted after being anchored **never raised any alert at all**
  for **any** org — `integrity_watchdog/tenancy.py`'s org-resolution
  join goes through the `steps` table, which is exactly the row that
  was just deleted. Fixed with a `steps_history.project_id` fallback
  (denormalized specifically to survive this).
- The deletion case's forensic attribution (`_forensic_evidence`) was
  never wired into the detector that actually finds deletions
  (`sweep_merkle_roots`) — it only ran from the edit-detection path.
- `_forensic_evidence`'s "most recent steps_history row" lookup could
  pick the wrong row on a same-second tie (`changed_at` is
  second-granularity) — fixed with an `id` tiebreaker.
- A user signing up via a valid invitation started `email_verified=false`,
  immediately blocking them from admin actions their invitation had just
  granted — redeeming a real emailed link is itself proof of inbox
  control, now reflected in `db.create_user`.
- `GET /alerts` (the list endpoint) never included `evidence` at all —
  only `GET /alerts/{id}` did — so the SDK's `alerts()` had nothing to
  expose typed forensic accessors over (Phase 4 G4).
- The scope needed for `POST /steps` is `logs:write`, not `steps:write`.
- `GET /trust-scores` 422s without a `run_id` query parameter.
