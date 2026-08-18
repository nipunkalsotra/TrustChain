"""org/project soft delete, membership provenance and role CHECK

Revision ID: d1e2f3a4b5c6
Revises: c2d3e4f5a6b7
Create Date: 2026-08-17 09:00:00.000000

Phase 3 §4.4/§4.5: organizations/projects gain deleted_at (soft delete —
hard deletion would orphan anchored steps whose Merkle proofs are still
independently verifiable on-chain, see the model docstring). memberships
gains invited_by (NULL for the founding owner and for every pre-Phase-3
row, backfilled below) and updated_at, plus a CHECK constraint on role
now that a value other than 'owner' can actually be written — nullable/
additive first per this repo's own migration convention, tightened only
where a CHECK can't be nullable by nature (role was never nullable to
begin with).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('deleted_at', sa.BigInteger(), nullable=True))
    op.add_column('projects', sa.Column('deleted_at', sa.BigInteger(), nullable=True))

    op.add_column('memberships', sa.Column('invited_by', sa.Integer(), nullable=True))
    op.add_column('memberships', sa.Column('updated_at', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_memberships_invited_by_users', 'memberships', 'users', ['invited_by'], ['id'],
    )
    # Backfill: every pre-Phase-3 membership's "last changed" is its own
    # creation — there is no earlier event to attribute it to.
    op.execute("UPDATE memberships SET updated_at = created_at WHERE updated_at IS NULL")

    op.create_check_constraint(
        'ck_memberships_role', 'memberships', "role IN ('owner','admin','member','viewer')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_memberships_role', 'memberships', type_='check')
    op.drop_constraint('fk_memberships_invited_by_users', 'memberships', type_='foreignkey')
    op.drop_column('memberships', 'updated_at')
    op.drop_column('memberships', 'invited_by')
    op.drop_column('projects', 'deleted_at')
    op.drop_column('organizations', 'deleted_at')
