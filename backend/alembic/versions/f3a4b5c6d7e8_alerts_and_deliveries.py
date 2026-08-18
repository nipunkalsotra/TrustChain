"""alerts, alert_deliveries, notification_preferences

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-17 09:10:00.000000

Phase 3 §7/§8.1. `alerts` is the raised-finding record; `alert_deliveries`
is a transactional outbox for notifying humans about it (same pattern as
`anchor_outbox`, ADR-0001 — a crash must never leave an alert nobody was
ever queued to be told about). `notification_preferences` defaults apply
by row-absence, so a brand-new member is opted into critical alerts from
the moment they join with no backfill needed.

The partial unique index on (dedupe_key WHERE status='open') is what
keeps a persistent problem as ONE row whose occurrence_count climbs,
instead of every watchdog sweep re-raising it as a fresh alert.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('alert_type', sa.String(length=60), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='open'),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('dedupe_key', sa.String(length=64), nullable=False),
        sa.Column('evidence_json', sa.Text(), nullable=True),
        sa.Column('detector', sa.String(length=40), nullable=True),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('first_seen_at', sa.BigInteger(), nullable=False),
        sa.Column('last_seen_at', sa.BigInteger(), nullable=False),
        sa.Column('acknowledged_at', sa.BigInteger(), nullable=True),
        sa.Column('acknowledged_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.BigInteger(), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('last_emailed_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id']),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id']),
        sa.CheckConstraint("severity IN ('critical','warning','info')", name='ck_alerts_severity'),
        sa.CheckConstraint("status IN ('open','acknowledged','resolved')", name='ck_alerts_status'),
    )
    op.create_index('ix_alerts_org_id', 'alerts', ['org_id'])
    op.create_index('ix_alerts_project_id', 'alerts', ['project_id'])
    op.create_index('ix_alerts_alert_type', 'alerts', ['alert_type'])
    op.create_index('ix_alerts_dedupe_key', 'alerts', ['dedupe_key'])
    op.create_index('ix_alerts_last_seen_at', 'alerts', ['last_seen_at'])
    op.create_index('ix_alerts_org_status', 'alerts', ['org_id', 'status', 'severity'])
    op.create_index(
        'uq_alerts_open_dedupe', 'alerts', ['dedupe_key'],
        unique=True, postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        'alert_deliveries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('alert_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False, server_default='email'),
        sa.Column('recipient', sa.String(length=320), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.BigInteger(), nullable=False),
        sa.Column('claimed_by', sa.String(length=64), nullable=True),
        sa.Column('claimed_at', sa.BigInteger(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('provider_message_id', sa.String(length=200), nullable=True),
        sa.Column('sent_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['alert_id'], ['alerts.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_alert_deliveries_alert_id', 'alert_deliveries', ['alert_id'])
    op.create_index('ix_alert_deliveries_pending', 'alert_deliveries', ['status', 'next_attempt_at'])

    op.create_table(
        'notification_preferences',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('email_critical', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('email_warning', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('email_info', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('email_digest_only', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'org_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
    )


def downgrade() -> None:
    op.drop_table('notification_preferences')
    op.drop_index('ix_alert_deliveries_pending', table_name='alert_deliveries')
    op.drop_index('ix_alert_deliveries_alert_id', table_name='alert_deliveries')
    op.drop_table('alert_deliveries')
    op.drop_index('uq_alerts_open_dedupe', table_name='alerts')
    op.drop_index('ix_alerts_org_status', table_name='alerts')
    op.drop_index('ix_alerts_last_seen_at', table_name='alerts')
    op.drop_index('ix_alerts_dedupe_key', table_name='alerts')
    op.drop_index('ix_alerts_alert_type', table_name='alerts')
    op.drop_index('ix_alerts_project_id', table_name='alerts')
    op.drop_index('ix_alerts_org_id', table_name='alerts')
    op.drop_table('alerts')
