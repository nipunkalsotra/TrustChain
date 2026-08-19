"""agents.last_verified_at / last_drift_at

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-08-17 09:25:00.000000

Phase 3 §6.2/§6.3/§8.2. Populated by the watchdog's identity detectors and
by POST /steps' synchronous drift check — GET /agents/{id}/integrity reads
these directly rather than aggregating rm_agent_events on every request.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('last_verified_at', sa.BigInteger(), nullable=True))
    op.add_column('agents', sa.Column('last_drift_at', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'last_drift_at')
    op.drop_column('agents', 'last_verified_at')
