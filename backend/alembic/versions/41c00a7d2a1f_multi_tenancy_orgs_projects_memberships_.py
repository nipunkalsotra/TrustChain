"""multi-tenancy: orgs, projects, memberships, api keys, idempotency, audit events

Revision ID: 41c00a7d2a1f
Revises: 2d9a96d1f2ab
Create Date: 2026-08-14 14:47:21.811152

"""
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41c00a7d2a1f'
down_revision: Union[str, Sequence[str], None] = '2d9a96d1f2ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('plan', sa.String(length=20), nullable=False),
    sa.Column('gas_budget_wei', sa.BigInteger(), nullable=True),
    sa.Column('gas_spent_wei', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('org_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=True),
    sa.Column('extra_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_created_at'), 'audit_events', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_events_org_id'), 'audit_events', ['org_id'], unique=False)
    op.create_table('memberships',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'org_id')
    )
    op.create_table('projects',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('environment', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_org_id'), 'projects', ['org_id'], unique=False)
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('last_four', sa.String(length=4), nullable=False),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.Column('expires_at', sa.BigInteger(), nullable=True),
    sa.Column('revoked_at', sa.BigInteger(), nullable=True),
    sa.Column('last_used_at', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_api_keys_key_hash'), 'api_keys', ['key_hash'], unique=True)
    op.create_index(op.f('ix_api_keys_project_id'), 'api_keys', ['project_id'], unique=False)
    op.create_table('idempotency_keys',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('response_json', sa.Text(), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    # Composite PK (project_id, key), not key alone — two different
    # tenants coincidentally choosing the same client-generated key
    # string must not collide. See db/models.py's IdempotencyKey docstring.
    sa.PrimaryKeyConstraint('project_id', 'key')
    )
    op.create_index(op.f('ix_idempotency_keys_created_at'), 'idempotency_keys', ['created_at'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('family_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.BigInteger(), nullable=False),
    sa.Column('expires_at', sa.BigInteger(), nullable=False),
    sa.Column('used_at', sa.BigInteger(), nullable=True),
    sa.Column('revoked_at', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_family_id'), 'refresh_tokens', ['family_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token_hash'), 'refresh_tokens', ['token_hash'], unique=True)

    # runs.project_id starts nullable — existing rows (if any) get backfilled
    # below, per-user, before it's tightened to NOT NULL. A single shared
    # "legacy" project for every pre-existing user would silently defeat
    # invariant I7 (tenant isolation) for exactly the data this migration
    # runs against, so each existing user gets their OWN org/project/
    # membership, identical in shape to what db.create_user() now does for
    # every new signup.
    op.add_column('runs', sa.Column('project_id', sa.Integer(), nullable=True))

    conn = op.get_bind()
    now = int(time.time())

    users = conn.execute(sa.text("SELECT id, name FROM users")).fetchall()
    for user_id, name in users:
        org_id = conn.execute(
            sa.text(
                "INSERT INTO organizations (name, plan, gas_spent_wei, created_at) "
                "VALUES (:name, 'free', 0, :now) RETURNING id"
            ),
            {"name": f"{name}'s Organization", "now": now},
        ).scalar_one()
        project_id = conn.execute(
            sa.text(
                "INSERT INTO projects (org_id, name, environment, created_at) "
                "VALUES (:org_id, 'Default', 'live', :now) RETURNING id"
            ),
            {"org_id": org_id, "now": now},
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO memberships (user_id, org_id, role, created_at) "
                "VALUES (:user_id, :org_id, 'owner', :now)"
            ),
            {"user_id": user_id, "org_id": org_id, "now": now},
        )
        conn.execute(
            sa.text(
                "UPDATE runs SET project_id = :pid "
                "WHERE project_id IS NULL AND user_email = (SELECT email FROM users WHERE id = :uid)"
            ),
            {"pid": project_id, "uid": user_id},
        )

    # Any run that still has no project_id (user_email was NULL, or didn't
    # match a known user — shouldn't happen via the app today, but a
    # migration must not silently drop or crash on data it doesn't expect)
    # goes into one shared orphan bucket rather than blocking the deploy.
    orphaned = conn.execute(sa.text("SELECT count(*) FROM runs WHERE project_id IS NULL")).scalar_one()
    if orphaned:
        org_id = conn.execute(
            sa.text(
                "INSERT INTO organizations (name, plan, gas_spent_wei, created_at) "
                "VALUES ('Legacy (unattributed)', 'free', 0, :now) RETURNING id"
            ),
            {"now": now},
        ).scalar_one()
        project_id = conn.execute(
            sa.text(
                "INSERT INTO projects (org_id, name, environment, created_at) "
                "VALUES (:org_id, 'Default', 'live', :now) RETURNING id"
            ),
            {"org_id": org_id, "now": now},
        ).scalar_one()
        conn.execute(
            sa.text("UPDATE runs SET project_id = :pid WHERE project_id IS NULL"),
            {"pid": project_id},
        )

    op.alter_column('runs', 'project_id', nullable=False)
    op.create_index(op.f('ix_runs_project_id'), 'runs', ['project_id'], unique=False)
    op.create_foreign_key('runs_project_id_fkey', 'runs', 'projects', ['project_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('runs_project_id_fkey', 'runs', type_='foreignkey')
    op.drop_index(op.f('ix_runs_project_id'), table_name='runs')
    op.drop_column('runs', 'project_id')
    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_family_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index(op.f('ix_idempotency_keys_created_at'), table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    op.drop_index(op.f('ix_api_keys_project_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_key_hash'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_index(op.f('ix_projects_org_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_audit_events_org_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_created_at'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('organizations')
