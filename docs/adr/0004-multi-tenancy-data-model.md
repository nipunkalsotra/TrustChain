# 0004 — Multi-tenancy data model (Organization/Project/Membership)

**Status:** Accepted

## Context

V1/Phase 1 TrustChain had one implicit tenant — a single deployment,
one set of contracts, no concept of "whose run is this". Turning
TrustChain into something third parties can register agents against and
log their own steps into (the SDK's whole reason to exist) requires
runs, steps, agents, and scores to belong to *someone*, and requires
that ownership to be enforceable, not just a convention.

## Decision

A three-level model: **Organization** → **Project** → **Membership**
(a user belongs to an org via a membership; a project belongs to an
org). Every user gets a personal org + project auto-provisioned at
signup (`db/tenancy.py::provision_personal_org`), in the **same
transaction** as the user row itself — a crash here must not leave a
user with no project, since every downstream principal resolution
assumes one always exists. `runs.project_id` is the scoping key
everything else hangs off: `steps` scope via their parent run,
`api_keys`/`idempotency_keys` carry `project_id` directly.

This is invariant **I7**: "no tenant can read or write another tenant's
runs, agents, or scores" — treated as a real, testable property (every
read/write path scoped by `project_id`, with a "not yours" run
indistinguishable from a "doesn't exist" run — no ID-probing signal),
not an informal assumption that happens to hold because nobody's built
a second tenant yet. See ADR-0006 for how this is enforced at the
database layer too, not just in application code.

## Alternatives considered

- **Flat "one user = one tenant"** (no organization layer). Rejected: a
  real customer is often a team, not an individual — org/project
  separation costs nothing extra to model now and avoids a much more
  disruptive migration later if/when team accounts are needed.
- **Tag runs with `user_email` instead of a real `project_id` foreign
  key.** This was V1's actual behavior for some fields, called out
  explicitly in the codebase as the wrong pattern ("not just informally
  associated by `user_email` like V1's `user_email` column was") —
  a string tag isn't enforceable at the database level (no FK, no
  constraint, easy to leave blank or spoof) the way a real
  `project_id` reference is.

## Consequences

- Every new tenant-scoped table added in the future must remember to
  carry (or resolve to) a `project_id` — nothing structurally prevents
  a future migration from adding an unscoped table by mistake. This is
  exactly the failure mode ADR-0006's Row-Level Security layer exists
  to catch even when application code forgets it.
- `api_keys` is deliberately **excluded** from Row-Level Security
  (ADR-0006) despite being tenant data, because resolving *which*
  project a credential belongs to is that table's own job — a
  project-scoped policy on it would make every API-key login return
  zero rows (the GUC that would need to be set doesn't exist yet,
  because this lookup is what determines it). Its protection is
  key-hash unguessability (SHA-256 of a securely-random raw key) plus
  application-level `project_id` filtering on the list/create/revoke
  endpoints specifically.
- A user has exactly one project today (auto-provisioned, embedded in
  the JWT so the API doesn't need a DB round-trip merely to
  authenticate — see `auth.py`'s module docstring). Multi-project-per-user
  isn't supported yet; adding it means either a project-switch flow with
  re-login/token-refresh, or moving `project_id` resolution out of the
  JWT entirely — a real, not-yet-needed architectural decision deferred
  until there's an actual multi-project use case.
