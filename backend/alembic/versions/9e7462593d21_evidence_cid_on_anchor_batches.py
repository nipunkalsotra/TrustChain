"""evidence cid on anchor batches

Revision ID: 9e7462593d21
Revises: e897e74cf66b
Create Date: 2026-09-07 13:43:59.810920

NULL means no evidence manifest was ever published for this batch —
either evidence_publisher_backend=disabled (the default: see
config.py/evidence/backends/disabled.py) or a real publish attempt
failed. Never a placeholder value — see anchor_worker/main.py's own
comment on why a failed/disabled publish leaves this column alone rather
than writing a fabricated URI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e7462593d21'
down_revision: Union[str, Sequence[str], None] = 'e897e74cf66b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('anchor_batches', sa.Column('evidence_cid', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('anchor_batches', 'evidence_cid')
