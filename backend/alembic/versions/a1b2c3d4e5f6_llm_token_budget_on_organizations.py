"""llm token budget on organizations

Revision ID: a1b2c3d4e5f6
Revises: 7c76a6e1b5ee
Create Date: 2026-08-15 17:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '7c76a6e1b5ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('token_budget', sa.BigInteger(), nullable=True))
    op.add_column('organizations', sa.Column('tokens_spent', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'tokens_spent')
    op.drop_column('organizations', 'token_budget')
