# 0020 — Database audit logging and attribution

**Status:** Accepted (per-operator credentials + DELETE audit coverage + content verification implemented; pgAudit-based statement-level logging proposed, not yet implemented — see "What remains open" below)

## Context

A user reviewing a real `step_row_tampered` alert email asked a
reasonable question: when TrustChain detects that a step's stored
content no longer matches its own hash, why doesn't the alert say who
tampered with it and what the row's content actually changed from/to?

Both turned out to be genuinely unavailable with the schema and threat
model as they stood, for two different, structural reasons — not
oversights fixable by a small code change alone:

**"What changed"** — `steps` stores `input_hash`/`output_hash`
(`Web3.solidity_keccak` digests), never the raw `input_text`/
`output_text` (`agents/base.py::log_step` hashes and never persists the
plaintext). This is very likely deliberate — TrustChain's job is proving
integrity, not warehousing potentially sensitive agent conversation
content — but its consequence is that even perfect history tracking
could only ever show "hash was X, now Y," never the original text,
because the original text was never durably stored anywhere to diff
against in the first place.

**"Who did it"** — the tampering this whole detection system exists to
catch is, by definition, a raw SQL statement run directly against
Postgres, bypassing the application entirely (this is exactly how every
tamper test in `tests/test_integrity_detectors.py` and this phase's
manual verification work — `UPDATE steps SET output_hash = ...` via
`psql`). `db/models.py`'s existing `AuditEvent` table already logs
"who did what" for admin actions (key issuance/revocation, membership
changes) per the plan's T3 threat mitigation — but only for actions that
go through the application's own code paths. There is no legitimate app
endpoint that edits `steps.output_hash` at all, so a raw DB edit can
never produce an `AuditEvent` row. No amount of *application*-layer
code change fixes this: if an attacker (or a rogue operator) has raw
Postgres access, the application has zero visibility into who they are,
full stop. Answering "who" needs a different layer — the database
connection/session itself.

## Decision (implemented)

**`steps_history` + `steps_audit_trigger`** (migration `b9a8a1970b3c`):
a Postgres `AFTER UPDATE ON steps` trigger, `SECURITY DEFINER` so it
keeps writing regardless of which role performs the tampering UPDATE,
records — for every `steps` row modification — which specific columns
changed (`input_hash`/`output_hash`/`leaf_hash`/`agent_code_hash`),
their old and new hash values, and three forensic-narrowing fields
captured from the actual invoking session: `session_user` (`db_role`),
`inet_client_addr()` (`db_client_addr`), and `application_name`. Since
`steps` rows are meant to be immutable after creation — no legitimate
app code path updates them — a row existing in `steps_history` at all is
itself already a strong signal something is wrong, independent of what
the diff says.

`integrity_watchdog/main.py::_forensic_evidence` looks up the
most-recent matching `steps_history` row whenever detector 3 raises a
`step_row_tampered` alert, and merges it into the alert's `evidence`
dict — which `notifications/templates.py` already renders generically
(no template change needed; it iterates whatever keys are present). A
step tampered with before this migration existed simply has no matching
history row, so this degrades gracefully to the pre-existing evidence
shape rather than erroring.

Two schema decisions worth calling out explicitly, both found by
actually running `tests/test_deleted_step_is_detected` against the
first draft of this migration rather than by inspection:

- `steps_history.step_id` is deliberately **not** a `ForeignKey` to
  `steps.id`. A hard FK would make it impossible to delete a `steps` row
  that has history — exactly backwards, since an attacker deleting the
  tampered step *to cover their tracks* is a real scenario this table
  should help investigate, not one it should block.
- `steps_history.project_id` is **denormalized onto the row at trigger
  time** (`SELECT r.project_id FROM runs r WHERE r.run_id = NEW.run_id`),
  not resolved via a join through `steps -> runs` at query time the way
  `steps`' own RLS policy does. A join-based policy would resolve to
  zero rows — hiding the tenant's own forensic record — for exactly the
  same "step later deleted" scenario above, since the join it depends on
  stops working once the step is gone. A value captured historically,
  while the join info still existed, doesn't have that problem.

This closes the "what changed" gap fully (going forward — it cannot
retroactively recover tampers that happened before this migration ran),
and partially narrows "who" — to a DB role and connecting address, not a
human identity, addressed below.

## Individually-issued DB credentials — the actual fix for "who" (implemented)

`anchor-worker`/`indexer`/`integrity-watchdog` correctly share the
`trustchain` superuser role — automated processes, one consistent
identity each, not a gap. The real gap was humans doing manual database
work also connecting as that same shared role, so `db_role` on any
tamper read `trustchain` regardless of *which* person it was —
real forensic narrowing (rules out `trustchain_api`, the RLS-bound role
the `api` service uses, as the source) but not the individual
attribution actually asked for.

**`scripts/db_operator.py`** (migration `010d34f64a31`) issues each
human their own `trustchain_op_<name>` role — `LOGIN SUPERUSER` (same
privilege level `trustchain` already had; this is about *accountability*
for what someone did, not about narrowing what any one operator can do)
with a freshly generated password shown exactly once, same convention as
`ApiKey`/`Invitation` raw tokens elsewhere. **`db_operators`** maps that
role name to a real display name. `integrity_watchdog/main.py::
_forensic_evidence` resolves it automatically — an alert's
`editedByOperator` field now names the actual person, not just a role
string, whenever they used their own issued credential. Verified for
real against the live stack: a genuine separate authenticated connection
as `trustchain_op_nipun` tampering a step produced an alert with
`"editedByOperator": "Nipun Kalsotra"`, delivered end-to-end by email.

The same migration also extended `steps_audit_trigger` to fire on
`DELETE`, not just `UPDATE` — without that, an attacker's best move to
leave zero forensic trail, even under per-operator credentials, is
simply to delete the tampered step outright rather than edit it.
Deletion now produces a `steps_history` row too (`changed_columns` is
the sentinel `["__deleted__"]`), with the same real attribution.

## Content verification without content storage (implemented)

A related question, asked directly by a user reading a real alert:
given `oldOutputHash`/`newOutputHash`, how does an owner actually learn
*what* the content was? They can't — a hash is one-way; nothing about
having the old and new hash *pairs* lets anyone recover text nobody kept
a copy of, TrustChain included, and that's unchanged by anything in this
ADR (see Context above on why raw text was never stored).

**`POST /integrity/verify-content`** closes the practical version of
that question instead: the owner supplies a *candidate* piece of text
they already have from their own systems (their own logs, an
observability tool, wherever their agent framework actually records
transcripts), and the endpoint hashes it identically to
`agents/base.py::log_step` and reports whether it matches the step's
current hash, and — via `steps_history` — whether it matches what the
hash was *before* the change. Verification of a supplied candidate, not
a search over unknown content. Verified for real against the live
stack's step #9 (tampered earlier in this session): the true original
text (`"world"`) correctly returned `matchesCurrent: false,
matchesOriginal: true`; a wrong guess returned `false` for both.

## What remains open

**`pgaudit`** (the Postgres extension, enabled via
`shared_preload_libraries` — requires a Postgres **restart**, a real
operational step not attempted here) would add statement-level logging
of every `UPDATE`/`DELETE`/`SELECT`/DDL platform-wide, correlated by the
watchdog against a detected tamper's timestamp window. Given
`steps_history` already captures `session_user`/`inet_client_addr()` per
row directly, and per-operator credentials now make `session_user`
genuinely mean something, pgAudit's remaining marginal value here is
breadth (full statement text, `SELECT`/DDL coverage, not just writes to
`steps`) rather than closing the specific "who tampered with this step"
question — real, but a separate, larger undertaking than what this ADR
set out to fix.

For a real AWS deployment (step I of this phase's rollout), IAM-
authenticated RDS connections (`aws rds generate-db-auth-token`) would
be a stronger version of the same per-operator idea used here — no
long-lived DB password to leak at all, and the IAM identity that
authenticated is externally auditable via CloudTrail independent of
Postgres itself. Not applicable to local/Anvil-based dev, which is why
`scripts/db_operator.py`'s plain-Postgres-role approach was built
instead — worth revisiting once a real RDS target exists.

## Alternatives considered

- **Store raw `input_text`/`output_text` in Postgres** so "before/after
  content" could be shown directly. Rejected — changes what data
  TrustChain persists at all (a real product/privacy decision, not a
  detection-mechanism one) and is out of scope for what was actually
  asked ("what changed," which hash-level diffing already answers
  faithfully without duplicating potentially sensitive agent content).
- **`SECURITY INVOKER` instead of `SECURITY DEFINER`** for the trigger
  function. Rejected — would require granting `INSERT` on
  `steps_history` to every role capable of tampering with `steps`,
  which defeats the purpose of an audit log whose writability shouldn't
  depend on the tampering role's own grants.
- **Application-level audit logging for step content** (e.g. logging
  every `log_step` call's arguments to a separate table). Doesn't help —
  the threat model here is specifically tampering that bypasses the
  application; an app-level log has the same blind spot `AuditEvent`
  already has for this case.

## Consequences

- `db/engine.py::truncate_all_tables()` had to explicitly add
  `steps_history` to its TRUNCATE list — it doesn't get pulled in via
  `CASCADE` the way most Phase 3 tables do, precisely because it has no
  FK for `CASCADE` to follow (a deliberate choice, see above). Any
  future audit-style table with the same "must outlive its subject row"
  property will need the same explicit addition.
- `steps_history` rows accumulate forever with no retention/pruning
  policy yet — fine at this phase's scale, worth revisiting once real
  production volume exists (same category of concern as Prometheus/Loki
  retention, not a new one).
- `db_operators` needed the same explicit-TRUNCATE and explicit-REVOKE
  treatment as `steps_history` — `trustchain_api` gets zero grants on it
  (pure ops/DBA metadata, not tenant or application data).
- Attribution is now only as good as operator discipline: anyone who
  falls back to the shared `trustchain` credential for a quick manual
  fix loses the individual-attribution benefit for that specific action,
  same as it always did. `scripts/db_operator.py` makes the right thing
  easy; it doesn't make the old shared credential stop working (a real
  tradeoff — revoking `trustchain`'s own login entirely would also break
  `anchor-worker`/`indexer`/`integrity-watchdog`, which legitimately
  need it).
- pgAudit remains the one genuinely open piece — see "What remains
  open" above.
