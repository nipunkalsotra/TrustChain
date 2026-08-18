"""steps_history audit trigger

Revision ID: b9a8a1970b3c
Revises: e8f9a0b1c2d3
Create Date: 2026-08-19 00:00:00.000000

Adds a forensic "what actually changed" record for tampering, distinct
from (and complementary to) the leaf-hash mismatch detection that already
exists: detector 3 (integrity_watchdog/detectors/step_rows.py) tells you
THAT a step's stored content no longer matches its own hash; this table
tells you WHICH columns were touched, their old vs new hash values, and
which DB role/client made the change — real gap identified from a user
asking for exactly this in an alert email, found to be genuinely
unavailable with the schema as it stood.

Populated by a Postgres TRIGGER, not application code — the whole point
is to catch modifications the application never made. `steps` rows are
meant to be immutable after creation (no legitimate app code path updates
them), so this trigger firing AT ALL is itself already a strong signal,
independent of what the diff says.

SECURITY DEFINER, owned by `trustchain` (this migration's own connecting
role, already the table owner) — deliberately NOT SECURITY INVOKER: an
audit trigger must keep writing regardless of which role performs the
tampering UPDATE, including a role that was never granted INSERT on
steps_history directly. `session_user`/`inet_client_addr()` are still
captured from the ORIGINAL invoking role/session inside the function body
(SECURITY DEFINER changes whose PRIVILEGES apply, not what session_user()
reports), so attribution data is unaffected by this choice.

What this does NOT solve: knowing session_user/inet_client_addr() tells
you which DB ROLE and CONNECTING ADDRESS performed the edit — genuinely
useful forensic narrowing — but not which HUMAN, if that role is shared
by multiple people (as `trustchain` currently is for anchor-worker/
indexer/manual ops). Real per-human attribution needs individually-issued
short-lived DB credentials, a separate, bigger decision — see
docs/adr/0020-database-audit-logging-and-attribution.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9a8a1970b3c'
down_revision: Union[str, Sequence[str], None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# UNLIKE steps' own policy (9f3a1c7d5e2b), this does NOT join through
# steps -> runs to resolve project_id at query time — project_id is
# denormalized onto the row itself instead, captured by the trigger at
# the moment it fires (see steps_audit_trigger() below). That's
# deliberate: the exact scenario this table exists to help investigate —
# an attacker who tampers with a step and then DELETES it entirely to
# cover their tracks (test_deleted_step_is_detected) — would make a
# join-based policy resolve to zero rows for a step that no longer
# exists, hiding the tenant's own forensic record of the tampering that
# happened right before the deletion. A value captured historically,
# when the join info still existed, doesn't have that problem.
_STEPS_HISTORY_POLICY_EXPR = (
    "current_setting('app.rls_bypass', true) = 'true' "
    "OR project_id = NULLIF(current_setting('app.current_project_id', true), '')::integer"
)


def upgrade() -> None:
    op.create_table(
        'steps_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        # Deliberately NOT a ForeignKey("steps.id") — see project_id's
        # comment above, same reasoning: this row must survive the
        # referenced step being deleted, not be blocked from doing so.
        sa.Column('step_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True, index=True),
        sa.Column('changed_at', sa.BigInteger(), nullable=False),
        sa.Column('changed_columns', sa.Text(), nullable=False),  # JSON array of column names
        sa.Column('old_input_hash', sa.String(66), nullable=True),
        sa.Column('new_input_hash', sa.String(66), nullable=True),
        sa.Column('old_output_hash', sa.String(66), nullable=True),
        sa.Column('new_output_hash', sa.String(66), nullable=True),
        sa.Column('old_leaf_hash', sa.String(66), nullable=True),
        sa.Column('new_leaf_hash', sa.String(66), nullable=True),
        sa.Column('old_agent_code_hash', sa.String(66), nullable=True),
        sa.Column('new_agent_code_hash', sa.String(66), nullable=True),
        # Forensic context — see module docstring's "what this does NOT solve".
        sa.Column('db_role', sa.String(64), nullable=True),
        sa.Column('db_client_addr', sa.String(64), nullable=True),
        sa.Column('db_application_name', sa.String(128), nullable=True),
    )
    op.create_index('ix_steps_history_step_id', 'steps_history', ['step_id'])
    op.create_index('ix_steps_history_changed_at', 'steps_history', ['changed_at'])

    op.execute("""
        CREATE OR REPLACE FUNCTION steps_audit_trigger() RETURNS trigger
        SECURITY DEFINER SET search_path = public
        AS $$
        DECLARE
            changed text[] := ARRAY[]::text[];
            resolved_project_id integer;
        BEGIN
            -- array_append(), not `changed || 'x'` — the || operator
            -- between an empty text[] and a bare string literal is
            -- ambiguous enough that Postgres's type resolver picks the
            -- wrong overload here and tries to parse the string AS an
            -- array literal ("malformed array literal" at runtime,
            -- found by actually running this trigger, not proofreading
            -- it) — array_append has no such ambiguity.
            IF NEW.input_hash IS DISTINCT FROM OLD.input_hash THEN changed := array_append(changed, 'input_hash'); END IF;
            IF NEW.output_hash IS DISTINCT FROM OLD.output_hash THEN changed := array_append(changed, 'output_hash'); END IF;
            IF NEW.leaf_hash IS DISTINCT FROM OLD.leaf_hash THEN changed := array_append(changed, 'leaf_hash'); END IF;
            IF NEW.agent_code_hash IS DISTINCT FROM OLD.agent_code_hash THEN changed := array_append(changed, 'agent_code_hash'); END IF;

            -- Resolved and stored on the row NOW, while steps/runs still
            -- joins cleanly — not re-derived later at query time, which
            -- is exactly what breaks once (or if) this step row is
            -- subsequently deleted. See _STEPS_HISTORY_POLICY_EXPR above.
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
        CREATE TRIGGER steps_audit_trigger_update
        AFTER UPDATE ON steps
        FOR EACH ROW
        EXECUTE FUNCTION steps_audit_trigger();
    """)

    # RLS: same tenant-isolation shape as `steps` itself.
    op.execute("ALTER TABLE steps_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE steps_history FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON steps_history "
        f"USING ({_STEPS_HISTORY_POLICY_EXPR}) WITH CHECK ({_STEPS_HISTORY_POLICY_EXPR})"
    )
    # 9f3a1c7d5e2b's ALTER DEFAULT PRIVILEGES already grants trustchain_api
    # SELECT/INSERT/UPDATE/DELETE on every NEW table in this schema,
    # steps_history included — broader than intended here. Nothing
    # application-side ever writes this table directly (only the trigger
    # above, running SECURITY DEFINER as the table owner, which bypasses
    # grants entirely) — narrow back down to read-only explicitly rather
    # than leave an unused write grant sitting on a forensic audit table.
    op.execute("REVOKE INSERT, UPDATE, DELETE ON steps_history FROM trustchain_api")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS steps_audit_trigger_update ON steps")
    op.execute("DROP FUNCTION IF EXISTS steps_audit_trigger()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON steps_history")
    op.drop_index('ix_steps_history_changed_at', table_name='steps_history')
    op.drop_index('ix_steps_history_step_id', table_name='steps_history')
    op.drop_table('steps_history')
