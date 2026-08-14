"""row-level security for tenant tables

Revision ID: 9f3a1c7d5e2b
Revises: 41c00a7d2a1f
Create Date: 2026-08-14 17:30:00.000000

Adds a second, DB-enforced layer under invariant I7 ("no tenant can read
or write another tenant's runs, agents, or scores") that holds even if a
future endpoint's Python code forgets a project_id filter — Postgres
Row-Level Security, keyed on the `app.current_project_id`/
`app.current_org_id` session GUCs that db/engine.py's `"begin"` event
listener sets from the request's resolved Principal (see auth.py).

WHY A SEPARATE `trustchain_api` ROLE: the table owner (and any superuser)
bypasses RLS unconditionally in Postgres, no matter how policies or FORCE
ROW LEVEL SECURITY are configured — there is no way to make RLS bind on
the same role that created the tables. `trustchain` (this migration's own
connecting role, same as anchor-worker/indexer/alembic) stays exactly as
privileged as before; only the NEW `trustchain_api` role — used solely by
the api service, see docker-compose.yml — is actually subject to these
policies. anchor-worker/indexer keep using `trustchain` because their
whole job (batch anchoring across tenants, indexing chain events into any
project's rows) is legitimately cross-tenant.

WHY `api_keys` IS NOT HERE: verify_api_key() (db/tenancy.py) looks up a
row by key_hash BEFORE any project_id is known — resolving which
project a credential belongs to is that table's entire job, so a
project_id-scoped policy would make every API-key login return zero
rows (chicken-and-egg: the GUC that would need to be set doesn't exist
yet, because this lookup is what determines it). key_hash is a SHA-256
digest of a securely-random raw key, so there's no enumeration/guessing
risk from leaving that lookup path unscoped; the endpoints that list/
create/revoke keys already filter by current_user.project_id in
application code (main.py), which is the correct control for those.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9f3a1c7d5e2b'
down_revision: Union[str, Sequence[str], None] = '41c00a7d2a1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES_BY_PROJECT = ["runs", "idempotency_keys"]
_TENANT_TABLES_BY_ORG = ["audit_events"]

# steps has no project_id column of its own — scoped via a join to runs,
# which is itself RLS-scoped by the same GUC (the subquery below is
# subject to runs' own policy too; both express the same condition, so
# that's consistent, not contradictory).
_STEPS_POLICY_EXPR = (
    "current_setting('app.rls_bypass', true) = 'true' "
    "OR run_id IN (SELECT run_id FROM runs WHERE project_id = "
    "NULLIF(current_setting('app.current_project_id', true), '')::integer)"
)


def _project_policy_expr(table: str) -> str:
    return (
        "current_setting('app.rls_bypass', true) = 'true' "
        f"OR {table}.project_id = NULLIF(current_setting('app.current_project_id', true), '')::integer"
    )


def _org_policy_expr(table: str) -> str:
    return (
        "current_setting('app.rls_bypass', true) = 'true' "
        f"OR {table}.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer"
    )


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'trustchain_api') THEN
                CREATE ROLE trustchain_api LOGIN PASSWORD 'trustchain_api_dev_password';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT CONNECT ON DATABASE trustchain TO trustchain_api")
    op.execute("GRANT USAGE ON SCHEMA public TO trustchain_api")
    # Broad table/sequence grants, same practical DML surface trustchain_api
    # had implicitly via the shared superuser role before this migration —
    # RLS (below) is what actually narrows visible ROWS on the tenant
    # tables; this just lets the role touch tables at all, tenant or not
    # (organizations, projects, anchor_batches, rm_scores, ...).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trustchain_api")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO trustchain_api")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trustchain_api"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO trustchain_api"
    )

    for table in _TENANT_TABLES_BY_PROJECT:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        expr = _project_policy_expr(table)
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")

    for table in _TENANT_TABLES_BY_ORG:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        expr = _org_policy_expr(table)
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")

    op.execute("ALTER TABLE steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE steps FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON steps USING ({_STEPS_POLICY_EXPR}) WITH CHECK ({_STEPS_POLICY_EXPR})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON steps")
    op.execute("ALTER TABLE steps NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE steps DISABLE ROW LEVEL SECURITY")

    for table in _TENANT_TABLES_BY_ORG + _TENANT_TABLES_BY_PROJECT:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM trustchain_api")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM trustchain_api")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM trustchain_api")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM trustchain_api")
    op.execute("REVOKE USAGE ON SCHEMA public FROM trustchain_api")
    op.execute("REVOKE CONNECT ON DATABASE trustchain FROM trustchain_api")
    op.execute("DROP ROLE IF EXISTS trustchain_api")
