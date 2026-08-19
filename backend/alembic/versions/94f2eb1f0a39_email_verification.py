"""email verification

Revision ID: 94f2eb1f0a39
Revises: 010d34f64a31
Create Date: 2026-08-19 12:00:00.000000

Phase 4 §3 step 1 (G1): there was no way to confirm a signup's email
address belongs to the person registering it, which every downstream
alert-email guarantee (Phase 3 §7/§8) silently assumed. `email_verified`
defaults false so every existing row starts unverified rather than
grandfathered in as trusted — a real deployment should decide separately
whether to backfill existing users as verified.

`email_verification_tokens` follows the exact discipline `invitations`
already established (migration e2f3a4b5c6d7, ADR-0014): only
sha256(token) is stored, single-use (`used_at` set via a conditional
UPDATE, see db/email_verification.py), expiring. Scoped by `user_id`,
not `org_id`/`project_id` — a user's email identity isn't tenant data
(same reasoning `refresh_tokens` and `users` themselves aren't RLS-scoped,
migration 9f3a1c7d5e2b), so this table isn't added to any RLS policy
list; it inherits ordinary trustchain_api grants via 9f3a1c7d5e2b's
`ALTER DEFAULT PRIVILEGES`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '94f2eb1f0a39'
down_revision: Union[str, Sequence[str], None] = '010d34f64a31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        'email_verification_tokens',
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
    op.create_index('ix_email_verification_tokens_user_id', 'email_verification_tokens', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_email_verification_tokens_user_id', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
    op.drop_column('users', 'email_verified')
