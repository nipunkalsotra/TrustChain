# 0006 — Row-Level Security as defense-in-depth under a separate DB role

**Status:** Accepted

## Context

Invariant I7 (ADR-0004) was enforced entirely in application code —
every `db`/`read_model` function takes a `project_id` and filters by
it. That's necessary but not sufficient: it depends on every current
*and future* query remembering to apply that filter, with nothing at
the database layer catching a mistake. A single missed `WHERE
project_id = ...` in a future endpoint is a silent cross-tenant data
leak, not a loud failure.

## Decision

Postgres Row-Level Security policies on the genuinely tenant-scoped
tables (`runs`, `steps` — via a join to its parent run's `project_id`,
`idempotency_keys`, `audit_events` — by `org_id`), keyed on
`app.current_project_id`/`app.current_org_id` session GUCs that
`db/engine.py`'s SQLAlchemy `"begin"` event listener sets from the
request's resolved Principal (`auth.py`) at the start of every
transaction. Crucially, RLS only actually *binds* for a
**non-owner, non-superuser role** — Postgres lets the table owner and
any superuser bypass RLS unconditionally, no matter what policies exist
— so a new `trustchain_api` role was created specifically for this
(the migration that adds the policies also creates it and grants it
table privileges). The API service connects as `trustchain_api`;
anchor-worker/indexer/alembic keep using the original `trustchain`
superuser, since their whole job (batch anchoring and indexing across
every tenant) is legitimately cross-tenant.

`GET /stats` (the one deliberately public, cross-tenant aggregate
endpoint) uses an explicit, narrow `rls_bypass()` context manager rather
than being exempted structurally — every use of it is a place that's
been read and justified, not a blanket escape hatch.

## Alternatives considered

- **Rely on application-level filtering alone, add more tests.** Tests
  catch what someone thought to test; they don't catch what nobody
  thought to test. RLS fails closed by default (`current_setting(...,
  true)` returns NULL when unset, and `NULL = anything` is never true in
  SQL) — a bug that skips a `project_id` filter *or* forgets to resolve
  a Principal at all still returns zero cross-tenant rows, not all of
  them.
- **RLS scoped to the SAME `trustchain` role the app already uses.**
  Doesn't work — table owner/superuser bypass RLS regardless of policy
  configuration; a distinct, deliberately-less-privileged role is a
  structural requirement, not a style choice.
- **Apply RLS to `api_keys` too**, since it's tenant data. Rejected —
  `verify_api_key()` looks up a row by `key_hash` *before* any
  `project_id` is known (resolving which project a credential belongs
  to is that lookup's entire job); a project_id-scoped policy on it
  would make every API-key login return zero rows. See ADR-0004's
  Consequences.

## Consequences

- Local dev and CI both need `alembic upgrade head` to have run (which
  creates `trustchain_api`) before the `api` service can start — this
  was already true for schema itself; the migration also had to be
  added retroactively to `.github/workflows/test.yml`'s
  `sdk-integration` job, which previously started the compose stack
  with no migration step at all (a real, separate gap this surfaced and
  fixed, not something this ADR's decision introduced).
- Two connection strings now exist for the same database: the
  superuser one (`.env`'s `DATABASE_URL`, used by pytest/alembic/anchor-
  worker/indexer — RLS-exempt) and `trustchain_api` (docker-compose's
  `api` service only, RLS-enforced). Anyone adding a new backend
  process that touches tenant tables needs to decide, deliberately,
  which role it should connect as.
- `tests/test_row_level_security.py` is the only test file that
  connects as `trustchain_api` directly — the rest of the suite
  (superuser) never exercises RLS at all, by design (most tests need
  cross-tenant fixture setup RLS would otherwise block). RLS itself is
  therefore only as trustworthy as that one dedicated test file staying
  comprehensive as new tenant-scoped tables are added.
