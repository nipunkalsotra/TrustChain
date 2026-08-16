"""agents current-state table

Revision ID: f1e2d3c4b5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-16 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('agents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('agent_id', sa.String(length=50), nullable=False),
    sa.Column('code_hash', sa.String(length=66), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('version', sa.String(length=50), nullable=False),
    sa.Column('registered_by', sa.String(length=42), nullable=False),
    sa.Column('registered_at', sa.BigInteger(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'agent_id', name='uq_agents_project_id_agent_id')
    )
    op.create_index(op.f('ix_agents_project_id'), 'agents', ['project_id'], unique=False)
    op.create_index(op.f('ix_agents_agent_id'), 'agents', ['agent_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_agents_agent_id'), table_name='agents')
    op.drop_index(op.f('ix_agents_project_id'), table_name='agents')
    op.drop_table('agents')
