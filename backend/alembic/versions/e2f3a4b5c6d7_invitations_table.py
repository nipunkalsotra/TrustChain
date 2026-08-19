"""invitations table

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-17 09:05:00.000000

Phase 3 §5.2. A bearer credential treated with the same discipline as
ApiKey.key_hash / RefreshToken.token_hash: only the SHA-256 hash is
stored, single-use, expiring, revocable. The partial unique index (not a
plain UniqueConstraint, which can't express "only when both these columns
are NULL") enforces at most one PENDING invitation per (org, email) —
re-inviting resends rather than creating a duplicate a UI would have to
dedupe itself.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'invitations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('invited_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('accepted_at', sa.BigInteger(), nullable=True),
        sa.Column('accepted_by', sa.Integer(), nullable=True),
        sa.Column('revoked_at', sa.BigInteger(), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('reminder_sent_at', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['invited_by'], ['users.id']),
        sa.ForeignKeyConstraint(['accepted_by'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id']),
        sa.UniqueConstraint('token_hash'),
        sa.CheckConstraint("role IN ('admin','member','viewer')", name='ck_invitations_role'),
    )
    op.create_index('ix_invitations_org_id', 'invitations', ['org_id'])
    op.create_index('ix_invitations_email', 'invitations', ['email'])
    op.create_index(
        'uq_invitations_pending', 'invitations', ['org_id', 'email'],
        unique=True, postgresql_where=sa.text('accepted_at IS NULL AND revoked_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_invitations_pending', table_name='invitations')
    op.drop_index('ix_invitations_email', table_name='invitations')
    op.drop_index('ix_invitations_org_id', table_name='invitations')
    op.drop_table('invitations')
