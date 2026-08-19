"""password reset tokens

Revision ID: c27c511c9d4c
Revises: 94f2eb1f0a39
Create Date: 2026-08-19 12:15:00.000000

Phase 4 §3 step 3 (G2): there was no recovery path for a forgotten
password — a user was permanently locked out. `password_reset_tokens`
reuses the exact shape 94f2eb1f0a39's email_verification_tokens just
established (itself modeled on ADR-0014's invitations): sha256(token)
only, single-use, expiring, user_id-scoped (not RLS'd — see
db/models.py::PasswordResetToken).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c27c511c9d4c'
down_revision: Union[str, Sequence[str], None] = '94f2eb1f0a39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
