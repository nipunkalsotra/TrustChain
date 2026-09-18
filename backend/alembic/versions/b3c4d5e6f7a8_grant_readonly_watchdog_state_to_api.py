"""grant read-only watchdog state access to trustchain_api

Revision ID: b3c4d5e6f7a8
Revises: 9e7462593d21
Create Date: 2026-09-18 23:50:00.000000

Migration d7e8f9a0b1c2 REVOKEd trustchain_api's access to watchdog_cursor
and batch_verifications entirely, on the reasoning that they're
integrity_watchdog's own operational state, not tenant data, and the API
role shouldn't be able to tamper with the tamper-detector's own bookkeeping
(ADR-0006's defense-in-depth). That's still the right call for writes.

But main.py's GET /integrity/status — itself written to serve tenants "is
everything OK right now" for their own project — reads both tables
directly under the trustchain_api-authenticated request, and its own
docstring says that's deliberate ("reads the watchdog's own persisted
cursor/coverage state ... rather than running anything live"). The full
REVOKE broke that read entirely: every call 500s with
InsufficientPrivilegeError, confirmed live against a real request from an
authenticated tenant with real anchored batches, not a hypothetical.

Splitting the difference: grant SELECT only, on both tables — the API can
now read watchdog's coverage/cursor state (what the feature needs) but
still can't INSERT/UPDATE/DELETE it (what the security boundary was
actually protecting against). integrity_watchdog itself keeps writing
through its own separate `trustchain` superuser connection, unaffected.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = '9e7462593d21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON watchdog_cursor TO trustchain_api")
    op.execute("GRANT SELECT ON batch_verifications TO trustchain_api")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON watchdog_cursor FROM trustchain_api")
    op.execute("REVOKE SELECT ON batch_verifications FROM trustchain_api")
