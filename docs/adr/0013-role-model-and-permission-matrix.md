# 0013 — Role model and permission matrix

**Status:** Accepted

## Context

Phase 2 had exactly one authorization check in the whole backend:
`main.py`'s `_require_admin()`, inlined as `role not in ("owner",
"admin")`, gating only API-key create/list/revoke. `memberships.role`
already existed as a free-text column, but nothing else ever wrote or
read a value other than `"owner"` — every user had exactly one
membership, created as owner, forever. Phase 3 gives orgs real multiple
members doing real things to them (inviting, changing roles, creating
projects, acknowledging alerts), which means "copy `_require_admin`'s
pattern at each new call site" stops being viable — it is exactly the
kind of gap invariant I7 (ADR-0004/0006) predicts for tenancy, applied
to authorization instead.

## Decision

Four org-level roles — `viewer < member < admin < owner` — with an
integer rank per role (`permissions.ROLE_RANK`) so "can actor X modify
target Y" is a plain comparison instead of a hand-rolled conditional at
each call site. Every permission a route can require is an entry in one
table, `permissions.MIN_ROLE_FOR: dict[Permission, str]` — this table
*is* the authorization policy, not documentation of one; a test
(`tests/test_permissions.py::test_every_mutating_route_is_covered_by_the_permission_matrix`)
walks FastAPI's real route table and fails the build if a mutating
handler's source contains none of `require_permission` /
`require_scope` / a documented direct-membership-check marker.

Roles are held at the **organization** level, not per-project — a
member's role is the same across every project in their org. Project-
scoped roles are representable later (the schema doesn't preclude it)
but aren't built now: nothing in the actual threat model or the
invitation flow needs finer granularity yet, and building it
speculatively would mean guessing at a shape before a real second use
case exists to design it against.

API keys keep their own, separate, scope-based authorization
(`agents:register`, `alerts:read`, etc., `db/tenancy.py::VALID_SCOPES`)
— a machine credential has no "role" concept, only whatever scopes it
was minted with. `auth.Principal` carries `user_id` (added this phase,
`None` for an API-key principal) specifically so the small number of
endpoints reachable by *either* credential type (`GET /alerts` and
friends) can apply the right mechanism for whichever credential showed
up, rather than forcing one unified check that fits neither case well.

## Alternatives considered

- **A single `is_admin: bool` flag instead of ranked roles.** Can't
  express "an admin may promote a viewer to member but not create
  another admin" — the whole point of `viewer`/`member` existing as
  distinct rungs below `admin` is lost.
- **Permission checks inlined at each route, like `_require_admin` was.**
  Exactly the gap this ADR exists to close — no single place to audit
  "what can a `member` do", and a copy-pasted check silently drifts from
  its original the moment one call site's requirement changes and
  another's doesn't.
- **Project-scoped roles from day one.** Rejected as premature — see
  Decision above.

## Consequences

- `memberships.role` gained a Postgres `CHECK` constraint
  (`ck_memberships_role`, migration `d1e2f3a4b5c6`) restricting it to
  the four known values — an application bug that writes a garbage role
  now fails loudly at the database rather than silently granting or
  denying access based on an unrecognized string.
- `_require_admin()` (API-key management) is now a one-line delegation
  to `permissions.require_permission`, not a separate mechanism —
  behavior for those three routes is unchanged.
- Every future mutating endpoint must either call
  `permissions.require_permission` (or `auth.require_scope` for an
  API-key-reachable one) or be added to `test_permissions.py`'s
  `_EXEMPT_PATHS` with a stated reason — the test makes silence the
  loud failure mode instead of the default one.
