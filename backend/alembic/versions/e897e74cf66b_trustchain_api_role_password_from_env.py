"""trustchain_api role password from env

Revision ID: e897e74cf66b
Revises: c27c511c9d4c
Create Date: 2026-09-07 11:50:58.739371

The RLS migration (9f3a1c7d5e2b) hardcoded
`CREATE ROLE trustchain_api LOGIN PASSWORD 'trustchain_api_dev_password'` —
fine for local dev (docker-compose.yml's `api` service connects with that
exact literal DATABASE_URL), but it means every deployment that has ever
run `alembic upgrade head`, including a real production one, ends up with
the identical, publicly-visible-in-this-repo password for the one Postgres
role Row-Level Security actually binds to (ADR-0006). Not fixed by editing
that historical migration in place (already-applied migrations shouldn't be
rewritten) — this adds a new one that ALTERs the role's password from
POSTGRES_API_PASSWORD if set, defaulting to the original dev literal when
it isn't, so local dev / CI (which never set it) see no behavior change at
all, while docker-compose.production.yml's required-env-var guard ensures a
real deployment always sets a real one before this migration ever runs
against it.
"""
import os
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e897e74cf66b'
down_revision: Union[str, Sequence[str], None] = 'c27c511c9d4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEV_DEFAULT_PASSWORD = "trustchain_api_dev_password"


def _sql_quote(password: str) -> str:
    """Standard SQL string-literal escaping (double any embedded single
    quote) — ALTER ROLE ... PASSWORD doesn't accept a bind parameter, so
    this can't go through a parameterized query the way DML normally would."""
    return password.replace("'", "''")


def upgrade() -> None:
    """Upgrade schema."""
    password = os.environ.get("POSTGRES_API_PASSWORD") or _DEV_DEFAULT_PASSWORD
    op.execute(f"ALTER ROLE trustchain_api PASSWORD '{_sql_quote(password)}'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"ALTER ROLE trustchain_api PASSWORD '{_sql_quote(_DEV_DEFAULT_PASSWORD)}'")
