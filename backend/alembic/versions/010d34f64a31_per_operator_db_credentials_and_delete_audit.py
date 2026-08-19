"""per-operator DB credentials and DELETE audit coverage

Revision ID: 010d34f64a31
Revises: b9a8a1970b3c
Create Date: 2026-08-19 01:00:00.000000

Closes the "who did it" gap ADR-0020 left open: `steps_history`
(migration b9a8a1970b3c) already captures `session_user`/
`inet_client_addr()` per row — real forensic data — but every human doing
manual database work currently connects under the SAME shared
`trustchain` superuser role, so that column reads `trustchain` regardless
of which person it actually was. This migration adds the metadata side
of the fix (`db_operators`, mapping a real display name to each
individually-issued role); `backend/scripts/db_operator.py` is the tool
that actually issues those roles (creating Postgres roles is not
something a migration should own — operators are added ad hoc, over
time, by whoever's running ops, not as part of a schema rollout).

Also extends steps_audit_trigger to fire on DELETE, not just UPDATE — a
real remaining gap in the attribution story otherwise: without this, an
attacker's best move to leave zero forensic trail (even under the new
per-operator-credential world) is simply to DELETE the tampered step
outright instead of editing it, since deletion previously produced no
steps_history row at all. Now it does (changed_columns is the sentinel
`["__deleted__"]`, all "new" hash columns NULL since there IS no new
row).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '010d34f64a31'
down_revision: Union[str, Sequence[str], None] = 'b9a8a1970b3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'db_operators',
        # The actual Postgres role name (e.g. 'trustchain_op_nipun') —
        # what steps_history.db_role will literally contain when this
        # person does anything, so it's the natural join key.
        sa.Column('role_name', sa.String(64), primary_key=True),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('revoked_at', sa.BigInteger(), nullable=True),
    )
    # Pure ops/DBA metadata, not tenant or application data — the `api`
    # service (trustchain_api) has no legitimate reason to read or write
    # this at all. 9f3a1c7d5e2b's ALTER DEFAULT PRIVILEGES would
    # otherwise silently grant it SELECT/INSERT/UPDATE/DELETE like every
    # other new table (same gotcha steps_history's migration hit).
    op.execute("REVOKE ALL ON db_operators FROM trustchain_api")

    op.execute("""
        CREATE OR REPLACE FUNCTION steps_audit_trigger() RETURNS trigger
        SECURITY DEFINER SET search_path = public
        AS $$
        DECLARE
            changed text[] := ARRAY[]::text[];
            resolved_project_id integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT r.project_id INTO resolved_project_id FROM runs r WHERE r.run_id = OLD.run_id;
                INSERT INTO steps_history (
                    step_id, project_id, changed_at, changed_columns,
                    old_input_hash, new_input_hash,
                    old_output_hash, new_output_hash,
                    old_leaf_hash, new_leaf_hash,
                    old_agent_code_hash, new_agent_code_hash,
                    db_role, db_client_addr, db_application_name
                ) VALUES (
                    OLD.id, resolved_project_id, extract(epoch from now())::bigint, '["__deleted__"]',
                    OLD.input_hash, NULL,
                    OLD.output_hash, NULL,
                    OLD.leaf_hash, NULL,
                    OLD.agent_code_hash, NULL,
                    session_user, host(inet_client_addr()), current_setting('application_name', true)
                );
                RETURN OLD;
            END IF;

            IF NEW.input_hash IS DISTINCT FROM OLD.input_hash THEN changed := array_append(changed, 'input_hash'); END IF;
            IF NEW.output_hash IS DISTINCT FROM OLD.output_hash THEN changed := array_append(changed, 'output_hash'); END IF;
            IF NEW.leaf_hash IS DISTINCT FROM OLD.leaf_hash THEN changed := array_append(changed, 'leaf_hash'); END IF;
            IF NEW.agent_code_hash IS DISTINCT FROM OLD.agent_code_hash THEN changed := array_append(changed, 'agent_code_hash'); END IF;

            SELECT r.project_id INTO resolved_project_id FROM runs r WHERE r.run_id = NEW.run_id;

            INSERT INTO steps_history (
                step_id, project_id, changed_at, changed_columns,
                old_input_hash, new_input_hash,
                old_output_hash, new_output_hash,
                old_leaf_hash, new_leaf_hash,
                old_agent_code_hash, new_agent_code_hash,
                db_role, db_client_addr, db_application_name
            ) VALUES (
                NEW.id, resolved_project_id, extract(epoch from now())::bigint, array_to_json(changed)::text,
                OLD.input_hash, NEW.input_hash,
                OLD.output_hash, NEW.output_hash,
                OLD.leaf_hash, NEW.leaf_hash,
                OLD.agent_code_hash, NEW.agent_code_hash,
                session_user, host(inet_client_addr()), current_setting('application_name', true)
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER steps_audit_trigger_delete
        AFTER DELETE ON steps
        FOR EACH ROW
        EXECUTE FUNCTION steps_audit_trigger();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS steps_audit_trigger_delete ON steps")
    # Restore the UPDATE-only function body from b9a8a1970b3c.
    op.execute("""
        CREATE OR REPLACE FUNCTION steps_audit_trigger() RETURNS trigger
        SECURITY DEFINER SET search_path = public
        AS $$
        DECLARE
            changed text[] := ARRAY[]::text[];
            resolved_project_id integer;
        BEGIN
            IF NEW.input_hash IS DISTINCT FROM OLD.input_hash THEN changed := array_append(changed, 'input_hash'); END IF;
            IF NEW.output_hash IS DISTINCT FROM OLD.output_hash THEN changed := array_append(changed, 'output_hash'); END IF;
            IF NEW.leaf_hash IS DISTINCT FROM OLD.leaf_hash THEN changed := array_append(changed, 'leaf_hash'); END IF;
            IF NEW.agent_code_hash IS DISTINCT FROM OLD.agent_code_hash THEN changed := array_append(changed, 'agent_code_hash'); END IF;

            SELECT r.project_id INTO resolved_project_id FROM runs r WHERE r.run_id = NEW.run_id;

            INSERT INTO steps_history (
                step_id, project_id, changed_at, changed_columns,
                old_input_hash, new_input_hash,
                old_output_hash, new_output_hash,
                old_leaf_hash, new_leaf_hash,
                old_agent_code_hash, new_agent_code_hash,
                db_role, db_client_addr, db_application_name
            ) VALUES (
                NEW.id, resolved_project_id, extract(epoch from now())::bigint, array_to_json(changed)::text,
                OLD.input_hash, NEW.input_hash,
                OLD.output_hash, NEW.output_hash,
                OLD.leaf_hash, NEW.leaf_hash,
                OLD.agent_code_hash, NEW.agent_code_hash,
                session_user, host(inet_client_addr()), current_setting('application_name', true)
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.drop_table('db_operators')
