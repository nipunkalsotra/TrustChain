"""block hash on anchor batches

Revision ID: c2d3e4f5a6b7
Revises: f1e2d3c4b5a6
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('anchor_batches', sa.Column('block_hash', sa.String(length=66), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('anchor_batches', 'block_hash')
