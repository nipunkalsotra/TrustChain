"""row-level security for Phase 3 tenant tables

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-08-17 09:30:00.000000

Extends migration 9f3a1c7d5e2b's RLS layer (ADR-0006) to every
tenant-scoped table Phase 3 adds, under the same trustchain_api role /
app.current_project_id / app.current_org_id GUC mechanism.

alerts, invitations, notification_preferences are scoped by org_id
(members of an org should see alerts/invitations/preferences across all
its projects, not just the one their session happens to be on).
alert_deliveries has no org_id of its own — scoped via a join to alerts,
the same shape 9f3a1c7d5e2b already uses for steps -> runs.

watchdog_cursor and batch_verifications are explicitly NOT tenant data —
that migration's `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES ...`
plus `ALTER DEFAULT PRIVILEGES` already gave trustchain_api implicit
access to every table created since, including these two operational
ones. Since neither has a tenant column an RLS policy could even key on,
the correct control here is to explicitly REVOKE trustchain_api's access
outright — the API service (and by extension any RLS-bound endpoint) has
no legitimate reason to touch watchdog internals; only the watchdog
itself (connecting as the `trustchain` superuser, same as anchor-worker
and indexer) does.

WHY invitations' PUBLIC preview lookup (GET /invitations/{token}) still
works despite RLS: that endpoint is unauthenticated, so no org_id GUC is
ever set for it. It runs its query inside an explicit
`app.rls_bypass = true` transaction (the same escape hatch
9f3a1c7d5e2b's policy expressions already define) with a hand-written
narrow SELECT — see db/invitations.py::get_invitation_preview. This is
the identical chicken-and-egg 9f3a1c7d5e2b documents for api_keys,
resolved the same way.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BY_ORG = ["alerts", "invitations", "notification_preferences"]

_ALERT_DELIVERIES_POLICY_EXPR = (
    "current_setting('app.rls_bypass', true) = 'true' "
    "OR alert_id IN (SELECT id FROM alerts WHERE org_id = "
    "NULLIF(current_setting('app.current_org_id', true), '')::integer)"
)


def _org_policy_expr(table: str) -> str:
    return (
        "current_setting('app.rls_bypass', true) = 'true' "
        f"OR {table}.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer"
    )


def upgrade() -> None:
    for table in _BY_ORG:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        expr = _org_policy_expr(table)
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({expr}) WITH CHECK ({expr})")

    op.execute("ALTER TABLE alert_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alert_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON alert_deliveries "
        f"USING ({_ALERT_DELIVERIES_POLICY_EXPR}) WITH CHECK ({_ALERT_DELIVERIES_POLICY_EXPR})"
    )

    # Operational, not tenant data — see module docstring. Undoes the
    # blanket default-privilege grant 9f3a1c7d5e2b gave trustchain_api on
    # every table created afterward, specifically for these two.
    op.execute("REVOKE ALL PRIVILEGES ON watchdog_cursor FROM trustchain_api")
    op.execute("REVOKE ALL PRIVILEGES ON batch_verifications FROM trustchain_api")


def downgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON batch_verifications TO trustchain_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON watchdog_cursor TO trustchain_api")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON alert_deliveries")
    op.execute("ALTER TABLE alert_deliveries NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alert_deliveries DISABLE ROW LEVEL SECURITY")

    for table in _BY_ORG:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
