"""steps.agent_code_hash + leaf_schema_version

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-17 09:20:00.000000

Phase 3 §6.2 — identity binding / leaf schema v2. Nullable-first, default
1, no backfill: every existing step keeps verifying under the original
(pre-Phase-3) leaf scheme forever, exactly as designed — see
blockchain/merkle.py's leaf_hash_v2 and its module docstring on why
mixing v1/v2 leaves in one Merkle tree is fine.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('steps', sa.Column('agent_code_hash', sa.String(length=66), nullable=True))
    op.add_column(
        'steps', sa.Column('leaf_schema_version', sa.SmallInteger(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('steps', 'leaf_schema_version')
    op.drop_column('steps', 'agent_code_hash')
