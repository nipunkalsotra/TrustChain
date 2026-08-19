"""notification_preferences.last_digest_sent_at

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-18 10:00:00.000000

Closes a real gap found during Phase 3 review: email_digest_only was
settable via PUT /me/notification-preferences from the start, but
nothing ever tracked or checked when a user's last digest went out —
notifications/digest.py needs this column to know who's actually due.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notification_preferences', sa.Column('last_digest_sent_at', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('notification_preferences', 'last_digest_sent_at')
