"""watchdog_cursor, batch_verifications

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-08-17 09:15:00.000000

Phase 3 §6.7/§8.1. Operational state for integrity_watchdog — NOT tenant
data (no org_id/project_id column, deliberately: see migration
d7e8f9a0b1c2, which explicitly denies trustchain_api any access to these
two tables rather than leaving them implicitly reachable through the
default-privilege grant every other new table gets).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'watchdog_cursor',
        sa.Column('detector', sa.String(length=40), nullable=False),
        sa.Column('last_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('wrapped_at', sa.BigInteger(), nullable=True),
        sa.Column('last_run_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('detector'),
    )

    op.create_table(
        'batch_verifications',
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('onchain_root_verified_at', sa.BigInteger(), nullable=True),
        sa.Column('onchain_root', sa.String(length=66), nullable=True),
        sa.Column('last_rebuilt_at', sa.BigInteger(), nullable=True),
        sa.Column('last_result', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('batch_id'),
        sa.ForeignKeyConstraint(['batch_id'], ['anchor_batches.id']),
    )


def downgrade() -> None:
    op.drop_table('batch_verifications')
    op.drop_table('watchdog_cursor')
