"""
db/models.py — SQLAlchemy 2.0 declarative models.

`users` / `runs` are Phase 2.0. `steps` / `anchor_outbox` / `anchor_batches`
/ `rm_scores` / `indexer_cursor` are Phase 2.1/2.2, added once Contracts
V2's actual event shapes existed to design them against (see the Phase 2
plan's note on why that ordering mattered — designing this schema before
AgentAuditLogV2/TrustScoreRegistryV2 existed would have meant guessing).

ONE NUANCE WORTH BEING PRECISE ABOUT: the plan's "read model is a pure
function of chain events, always rebuildable from genesis" invariant (I6)
applies cleanly to `rm_scores` — TrustScoreRegistryV2's ScoreUpdated event
carries the agent, run, score and reason in full, so replaying every event
reconstructs the table exactly. It does NOT apply the same way to `steps`:
AgentAuditLogV2 only ever anchors a Merkle ROOT on-chain, never individual
step content (that's the entire point of batching — see merkle.py). So
`steps` is off-chain SOURCE OF TRUTH for what an agent actually did,
written by the orchestrator, not derived from chain at all. What the
indexer *can* rebuild from chain is each step's ANCHORING STATUS (which
batch covered it, at what tx/block) — via `anchor_batches`, populated from
BatchAnchored events. Losing the `steps` table loses the audit content
itself (mitigated by ordinary Postgres backups, not by chain replay); losing
`anchor_batches`' confirmation columns only loses re-derivable metadata.
"""

from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, JSON, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Phase 4 G1 — defaults False for every row, including ones that
    # existed before this column did (migration 94f2eb1f0a39); a real
    # deployment backfilling pre-existing users as verified is a separate,
    # conscious decision, not something this column's default should make
    # for it. See permissions.py::REQUIRES_VERIFIED_EMAIL for what an
    # unverified account is blocked from doing.
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EmailVerificationToken(Base):
    """A bearer credential proving control of `users.email` — same
    discipline as Invitation.token_hash / RefreshToken.token_hash: only
    sha256(token) is stored, single-use (`used_at` set via a conditional
    UPDATE, see db/email_verification.py), expiring. Scoped by user_id,
    not org_id — a user's email identity isn't tenant data, so (like
    `users`/`refresh_tokens`) this table carries no RLS policy."""

    __tablename__ = "email_verification_tokens"

    id:         Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id:    Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_at:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class PasswordResetToken(Base):
    """A bearer credential authorizing a password change for one user —
    same discipline as EmailVerificationToken above, but shorter-lived
    (config.password_reset_ttl_seconds, default 1 hour vs. 24) since this
    one is a live account-takeover credential if intercepted, not merely
    proof of inbox control. Scoped by user_id, no RLS policy, same
    reasoning as EmailVerificationToken."""

    __tablename__ = "password_reset_tokens"

    id:         Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id:    Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_at:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Organization(Base):
    """
    Billing and isolation boundary (Phase 2 plan §14.1). Every user gets one
    auto-provisioned at signup (db.create_user) — invisible to the current
    frontend, which never selects an org, but real underneath: every run,
    step, and score a user creates is scoped to their org's default project
    (invariant I7), not just informally associated by user_email like
    Phase 1/2.0-2.2. API keys (for SDK/third-party consumers) issue against
    a project and never see other orgs' data, enforced the same way.
    """

    __tablename__ = "organizations"

    id:             Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name:           Mapped[str] = mapped_column(String(200), nullable=False)
    plan:           Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    gas_budget_wei: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    gas_spent_wei:  Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # LLM token budget (plan O10: "LLM token budgets ... are what actually
    # bound worst-case spend") — same nullable-ceiling/cumulative-spend
    # shape as gas_budget_wei/gas_spent_wei above, checked and updated by
    # db/tenancy.py's get_org_token_budget_status/record_token_spend.
    token_budget:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    tokens_spent:   Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at:     Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Soft delete (Phase 3 §4.4): hard deletion would orphan anchored steps
    # whose Merkle proofs are still independently verifiable on-chain —
    # destroying the off-chain half of a published proof is not a thing a
    # DELETE endpoint should do. Every list/read path filters this NULL.
    deleted_at:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Project(Base):
    """Scoping unit within an org. `environment` distinguishes test/live API
    keys the same way Stripe-style platforms do — a test-environment project
    can point its SDK consumers at a different (e.g. local Anvil) chain
    without touching live data, though this phase only wires the column
    through, not a per-environment chain-routing feature."""

    __tablename__ = "projects"

    id:           Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id:       Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name:         Mapped[str] = mapped_column(String(200), nullable=False)
    environment:  Mapped[str] = mapped_column(String(20), nullable=False, default="live")  # test | live
    created_at:   Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # see Organization.deleted_at


class Membership(Base):
    """Which users may act on behalf of which org, and how much authority
    they have. `role` is checked in application code today (owner/admin can
    issue+revoke API keys; member cannot) — not yet enforced by Postgres RLS
    (see db/models.py module note on that being a defense-in-depth layer
    still open, tracked as a known gap rather than silently assumed done)."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner','admin','member','viewer')", name="ck_memberships_role"),
    )

    user_id:    Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    org_id:     Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), primary_key=True)
    role:       Mapped[str] = mapped_column(String(20), nullable=False, default="owner")  # owner|admin|member|viewer
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Provenance (Phase 3 §4.5): NULL for the founding owner (nobody
    # invited them) and for pre-Phase-3 rows backfilled by migration.
    invited_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class ApiKey(Base):
    """
    Machine credential for SDK/third-party consumers. Only `key_hash` (a
    plain SHA-256 hex digest — HMAC would need a secret this table doesn't
    have anywhere safer to keep) is stored; the raw key is shown exactly
    once, at creation, in the API response — never logged, never
    recoverable afterward. `last_four` lets a UI/CLI list keys
    identifiably ("tc_live_...a1b2") without ever re-displaying the secret.
    """

    __tablename__ = "api_keys"

    id:            Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id:    Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    key_hash:      Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    last_four:     Mapped[str] = mapped_column(String(4), nullable=False)
    scopes:        Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    revoked_at:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_used_at:  Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class IdempotencyKey(Base):
    """Replay protection for mutating endpoints (POST /run-agent, SDK
    tc.log()). `request_fingerprint` (hash of method+path+body) is stored
    alongside the response so a same-key-different-body replay is
    detectable and rejected (409) rather than silently returning a stale
    response for a different request — see db/idempotency.py.

    Primary key is (project_id, key), not key alone: an idempotency key
    is client-generated (the SDK defaults to a UUIDv4, but a caller can
    supply anything), so two DIFFERENT tenants coincidentally choosing the
    same string must not collide — a bare `key` PK would make that string
    globally claimed by whichever project used it first, silently 409ing
    every other tenant that ever picks the same value."""

    __tablename__ = "idempotency_keys"

    project_id:           Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), primary_key=True)
    key:                  Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint:  Mapped[str] = mapped_column(String(64), nullable=False)
    response_json:        Mapped[str] = mapped_column(Text, nullable=False)
    status_code:          Mapped[int] = mapped_column(Integer, nullable=False)
    created_at:           Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class AuditEvent(Base):
    """Platform-level admin action log — who did what, distinct from the
    on-chain agent audit log (steps/anchors). Covers key issuance/
    revocation, membership changes, and other authority-affecting actions,
    per the plan's T3 (insider/operator) threat mitigation: 'admin actions
    are themselves audited'."""

    __tablename__ = "audit_events"

    id:         Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id:   Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    org_id:     Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    action:     Mapped[str] = mapped_column(String(100), nullable=False)
    target:     Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class RefreshToken(Base):
    """
    Rotating refresh token with reuse detection (plan §11.3: 'Refresh-token
    reuse detection revokes the family'). Only `token_hash` is stored (the
    raw token is bearer-secret, shown once at issuance) — additive new auth
    capability alongside the existing long-lived primary JWT (see auth.py's
    module docstring for why the primary token isn't shortened yet).

    `family_id` links every refresh token descended from one login. Each
    rotation marks the OLD token `used_at` and issues a new one in the same
    family; if a token with `used_at` already set is presented again, that
    is a replay (the attacker has a copy of an already-rotated token) —
    the entire family is revoked, forcing re-authentication rather than
    letting the thief's token silently keep working.
    """

    __tablename__ = "refresh_tokens"

    id:          Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id:     Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    family_id:   Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash:  Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at:  Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at:  Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_at:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    revoked_at:  Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Run(Base):
    __tablename__ = "runs"

    run_id:       Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id:   Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    task:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_email:   Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    status:       Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    result_json:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    completed_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class AnchorBatch(Base):
    """
    One Merkle-batch's full lifecycle, in one row — building (worker just
    claimed steps and computed a root) -> submitted (tx sent, awaiting
    confirmation) -> confirmed (indexer observed BatchAnchored) -> failed
    (permanently, after retries exhausted).

    `leaf_order` is the exact, ordered list of step_ids used to build the
    Merkle tree — persisted, not re-derived later by re-querying steps. A
    "re-sort by created_at again" approach is fragile if the query or table
    ever changes shape; recording the tree's own membership and order here
    means the worker (or anyone auditing later) can always reproduce the
    exact same root from the exact same steps, regardless of what the
    steps table looks like by then.
    """

    __tablename__ = "anchor_batches"

    id:               Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id_hash:      Mapped[str] = mapped_column(String(66), nullable=False, index=True)  # 0x + 64 hex
    # NOT unique: a batch that fails before confirming (revert, timeout)
    # is abandoned and its steps requeued for rebatching (see
    # anchor_worker/main.py::handle_submit_failure) — an unchanged leaf
    # set deterministically reproduces the same root on retry, so more
    # than one row can legitimately carry it (at most one non-'failed').
    # Replay protection lives on-chain, in AgentAuditLogV2.usedRoots,
    # which only ever gets set by a root that actually confirmed.
    merkle_root:      Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    step_count:       Mapped[int] = mapped_column(Integer, nullable=False)
    leaf_order:       Mapped[list] = mapped_column(JSON, nullable=False)  # ordered [step_id, ...]
    status:           Mapped[str] = mapped_column(String(20), nullable=False, default="building", index=True)
    # building | submitted | confirmed | failed
    tx_hash:          Mapped[Optional[str]] = mapped_column(String(66), nullable=True, index=True)
    onchain_anchor_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    block_number:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # Same reorg-detection reasoning as IndexerCursor.last_block_hash
    # above: block_number ALONE can be silently invalidated by a reorg —
    # a stored number that no longer matches the chain's actual block at
    # that height means this batch's confirmation may have been on a
    # since-abandoned fork. Storing the hash alongside the number is what
    # lets a caller (or a future reconciliation check) actually detect
    # that, rather than trusting a number that could point at nothing
    # real anymore.
    block_hash:       Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    # Real gas-spend attribution (plan §11.4/O10) — the REAL cost this
    # specific batch's confirmation actually incurred, read straight off
    # the transaction receipt (gasUsed) and the receipt's own
    # effectiveGasPrice (the actual EIP-1559 price paid, not the
    # maxFeePerGas ceiling submit.py was willing to pay — see
    # blockchain/gas.py) — not estimated, not the RBF attempt's asking
    # price if a replacement was needed. gas_used * gas_price_wei = real
    # wei spent on this one confirmation; joined through
    # steps.anchor_batch_id -> runs.project_id, this is what lets a
    # project see its own real cumulative on-chain spend (GET /gas-spend).
    gas_used:         Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    gas_price_wei:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_error:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at:       Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    confirmed_at:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Step(Base):
    """
    One auditable agent action. Off-chain source of truth for content (see
    module docstring) — what the chain anchors is this row's `leaf_hash`,
    batched with others under some AnchorBatch's merkle_root.
    """

    __tablename__ = "steps"

    id:              Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id:          Mapped[str] = mapped_column(String(64), ForeignKey("runs.run_id"), nullable=False, index=True)
    agent_id:        Mapped[str] = mapped_column(String(50), nullable=False)
    step_index:      Mapped[int] = mapped_column(Integer, nullable=False)
    action:          Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash:      Mapped[str] = mapped_column(String(66), nullable=False)
    output_hash:     Mapped[str] = mapped_column(String(66), nullable=False)
    leaf_hash:       Mapped[str] = mapped_column(String(66), nullable=False, unique=True)
    metadata_json:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp:       Mapped[int] = mapped_column(BigInteger, nullable=False)  # matches leaf preimage exactly
    anchor_batch_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("anchor_batches.id"), nullable=True, index=True
    )
    created_at:      Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Identity binding (Phase 3 §6.2 — leaf schema v2): the SDK's own
    # fingerprint of the agent that produced this step, carried into the
    # Merkle leaf preimage for v2 rows so a database-level edit of THIS
    # column can never make a step appear to have been produced by a
    # different, unregistered identity without also breaking the anchored
    # hash. NULL for steps logged by an SDK that predates this field —
    # those stay leaf_schema_version=1 and verify under the original
    # (pre-identity-binding) scheme forever; nothing about existing
    # anchored proofs changes retroactively. See blockchain/merkle.py.
    agent_code_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    leaf_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)


class StepHistory(Base):
    """
    Append-only forensic record of any UPDATE that touches a `steps` row
    — populated ONLY by the `steps_audit_trigger` Postgres trigger
    (migration b9a8a1970b3c), never by application code. `steps` rows
    are meant to be immutable after creation, so a row existing here at
    all is itself already a strong signal something is wrong,
    independent of what the diff says. Complements (not replaces)
    integrity_watchdog/detectors/step_rows.py's leaf-hash mismatch check
    — that detector proves THAT a row no longer matches its own hash;
    this table records WHICH columns changed, their old/new hash values,
    and the DB role/client that made the change, surfaced in the alert's
    evidence by integrity_watchdog/main.py::_raise_step_row_alerts.

    See this table's own migration docstring for what db_role/
    db_client_addr can and can't prove about WHO made a change.

    step_id is deliberately NOT a ForeignKey to steps.id, and project_id
    is denormalized here (resolved and stored by the trigger at write
    time) rather than resolved via a join through steps -> runs — both
    for the same reason: this record must survive the referenced step
    later being DELETED entirely (an attacker covering their tracks,
    exactly what test_deleted_step_is_detected simulates), not be
    blocked by a FK, and not become invisible to RLS once the join it
    would depend on no longer resolves.
    """

    __tablename__ = "steps_history"

    id:                  Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    step_id:             Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    project_id:          Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    changed_at:          Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    changed_columns:     Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of column names
    old_input_hash:       Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    new_input_hash:       Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    old_output_hash:      Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    new_output_hash:      Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    old_leaf_hash:        Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    new_leaf_hash:        Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    old_agent_code_hash:  Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    new_agent_code_hash:  Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    db_role:              Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    db_client_addr:       Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    db_application_name:  Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class DbOperator(Base):
    """
    Maps an individually-issued Postgres role (e.g. 'trustchain_op_nipun',
    created by scripts/db_operator.py, never by application code) to a
    real human's display name — the piece ADR-0020 ("database audit
    logging and attribution") identified as actually missing: steps_
    history.db_role already captures session_user correctly, but that
    column only distinguishes individual PEOPLE if they each connect
    under their own role rather than sharing one. Pure ops/DBA metadata,
    not tenant or application data — trustchain_api has no grants on this
    table at all (migration 010d34f64a31 explicitly revokes the default
    privileges every new table otherwise gets).
    """

    __tablename__ = "db_operators"

    role_name:    Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at:   Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    revoked_at:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class AnchorOutbox(Base):
    """
    Durable intent to anchor a step, written in the same DB transaction as
    the Step row it refers to (see agents/base.py::log_step). This is what
    makes anchoring at-least-once rather than best-effort: a crash between
    "wrote the step" and "told the chain about it" is impossible to
    observe from outside — either both rows commit, or neither does.
    """

    __tablename__ = "anchor_outbox"

    id:              Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    step_id:         Mapped[int] = mapped_column(Integer, ForeignKey("steps.id"), nullable=False, unique=True)
    status:          Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # pending | claimed | anchored | dead_letter
    attempts:        Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_by:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claimed_at:      Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    last_error:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    batch_id:        Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("anchor_batches.id"), nullable=True)
    created_at:      Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReadModelScore(Base):
    """
    Pure read model, mirroring TrustScoreRegistryV2's ScoreUpdated events —
    unlike `steps`, this table genuinely IS a pure function of chain events
    and can be truncated and rebuilt from genesis at any time (invariant
    I6), since the event itself carries the full row content.
    """

    __tablename__ = "rm_scores"
    # (tx_hash, log_index) is the event's own on-chain identity — unique
    # so a cursor rewind after a detected reorg (indexer/cursor.py) can
    # safely reprocess an overlapping block range without double-inserting
    # a row for an event it already indexed.
    __table_args__ = (UniqueConstraint("tx_hash", "log_index", name="uq_rm_scores_tx_hash_log_index"),)

    id:           Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id:     Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    run_id:       Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    score:        Mapped[int] = mapped_column(Integer, nullable=False)
    reason:       Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # ScoreUpdated's OWN `timestamp` arg (block.timestamp when the score was
    # set) — distinct from indexed_at (when the indexer got around to
    # processing it), which can lag behind by up to indexer_poll_interval_seconds.
    # GET /trust-scores/history's ScoreHistoryPoint.timestamp is this one.
    timestamp:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash:      Mapped[str] = mapped_column(String(66), nullable=False)
    log_index:    Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_at:   Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReadModelAgentEvent(Base):
    """
    Pure read model, mirroring AgentIdentityRegistryV2's AgentRegistered /
    AgentRevoked / IntegrityViolation events — one table for all three
    (event_type discriminates) rather than three tables, since they share
    the same "one agent, one point-in-time occurrence" shape and no query
    so far needs more than agent_id + event_type to filter. Same invariant
    I6 rebuildable-from-genesis property as rm_scores (see ReadModelScore's
    docstring): every field is carried in full by the event itself.

    project_id: the contract's own on-chain namespacing key (see
    AgentIdentityRegistryV2.sol's project-namespacing docstring) — two
    tenants can register the same agent_id, so agent_id ALONE is not a
    safe filter for a per-tenant view of this table; (project_id,
    agent_id) is the real composite identity, matching the contract's own
    (projectId, agentId) mapping key.
    """

    __tablename__ = "rm_agent_events"
    __table_args__ = (UniqueConstraint("tx_hash", "log_index", name="uq_rm_agent_events_tx_hash_log_index"),)

    id:            Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type:    Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    project_id:    Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    agent_id:      Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # actor: registeredBy (AgentRegistered) or revokedBy (AgentRevoked);
    # NULL for IntegrityViolation, which the contract emits with no actor
    # arg (verifyAgentAndLog can be called by anyone — it's a check, not a
    # privileged action).
    actor:         Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    code_hash:     Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    expected_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    provided_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    timestamp:     Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_number:  Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash:       Mapped[str] = mapped_column(String(66), nullable=False)
    log_index:     Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)


class Agent(Base):
    """
    Current materialized state, one row per (project_id, agent_id) —
    unlike ReadModelAgentEvent above (an append-only event log a "list
    my agents" query would otherwise have to aggregate from scratch on
    every request: latest AgentRegistered/AgentUpdated vs. a later
    AgentRevoked), this table is upserted in place by the indexer
    (indexer/agent_events.py) so GET /agents (db/read_model.py::
    list_agents) is a single indexed SELECT.

    Unlike rm_scores/ReadModelAgentEvent, this is NOT populated purely
    from event args — neither AgentRegistered nor AgentUpdated actually
    carries modelName/modelVersion (check AgentIdentityRegistryV2.sol's
    event definitions), so indexer/agent_events.py does a live
    getAgent() read whenever any identity-changing event fires and
    upserts the chain's real current record. See that module's own
    docstring for why a live read is correct here specifically — this
    table represents "what's true right now," not history, so
    rebuilding it from genesis by re-deriving current state at each
    step converges to the same correct end state a pure-event replay
    would, just without the (here, irrelevant) point-in-time fidelity
    rm_agent_events itself still preserves.
    """

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_agents_project_id_agent_id"),)

    id:            Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id:    Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    agent_id:      Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    code_hash:     Mapped[str] = mapped_column(String(66), nullable=False)
    model:         Mapped[str] = mapped_column(String(200), nullable=False, default="")
    version:       Mapped[str] = mapped_column(String(50), nullable=False, default="")
    registered_by: Mapped[str] = mapped_column(String(42), nullable=False)
    registered_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active:     Mapped[bool] = mapped_column(nullable=False, default=True)
    updated_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Populated by the integrity watchdog's detector 1/2 (Phase 3 §6.2-6.3)
    # and by POST /steps' synchronous drift check — GET /agents/{id}/integrity
    # reads these directly rather than aggregating rm_agent_events on every
    # request. last_drift_at is intentionally sticky (never cleared
    # automatically) — "this agent has drifted before" stays visible even
    # after the immediate cause is fixed, until someone reviews the alert.
    last_verified_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_drift_at:     Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class IndexerCursor(Base):
    """
    One row per contract the indexer follows — (block_number, block_hash)
    it last processed up to, so it can detect a reorg (the chain's block at
    that height no longer matches what's recorded here) and rewind/replay
    rather than silently drifting from chain truth.
    """

    __tablename__ = "indexer_cursor"

    contract_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_block:    Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_block_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    updated_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 3 — organizations, roles & continuous integrity monitoring
# ─────────────────────────────────────────────────────────────────────────────

class Invitation(Base):
    """
    A bearer credential granting membership in an org, treated with the
    same discipline as ApiKey.key_hash / RefreshToken.token_hash above:
    only the SHA-256 hash is stored, the raw token exists exactly once (in
    the invitation email), single-use (accepted_at set atomically via a
    conditional UPDATE, see db/invitations.py), expiring, and revocable.

    `role` is never 'owner' — ownership is transferred (see
    Membership/db/orgs.py::transfer_ownership), not granted from an
    invitation, so "how many owners does this org have" is never a
    function of who happened to accept what.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin','member','viewer')", name="ck_invitations_role"),
        # At most one PENDING invitation per (org, email) — re-inviting
        # resends rather than creating a duplicate row a UI would have to
        # dedupe itself. A partial index (not a plain UniqueConstraint,
        # which can't express "only when both these columns are NULL")
        # is the only way to encode this in Postgres.
        Index(
            "uq_invitations_pending", "org_id", "email",
            unique=True, postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id:            Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id:        Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    email:         Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role:          Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash:    Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by:    Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at:   Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    accepted_by:   Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    revoked_at:    Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    revoked_by:    Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reminder_sent_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Alert(Base):
    """
    A raised integrity/security finding. `dedupe_key` (sha256 of
    alert_type:project_id:subject) plus the partial unique index in
    migration g7h8i9j0k1l2 (WHERE status='open') is what keeps a
    persistent problem as ONE row whose occurrence_count climbs, instead
    of the rolling watchdog sweep re-raising it every cycle — see Phase 3
    plan §7.2. Written in the SAME transaction as its alert_deliveries
    rows (integrity_watchdog/raise_alert.py) for the same reason
    log_step writes steps+anchor_outbox together (ADR-0001): a crash must
    never be able to leave a recorded alert nobody was ever queued to be
    told about.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("severity IN ('critical','warning','info')", name="ck_alerts_severity"),
        CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_alerts_status"),
        # At most one OPEN alert per dedupe_key — this is what makes the
        # watchdog's rolling sweep re-raising the same finding every cycle
        # increment occurrence_count on one row instead of flooding the
        # table (and the inbox) with duplicates. A resolved alert frees
        # the key so a genuine recurrence raises a fresh one (Phase 3 §7.2).
        Index("uq_alerts_open_dedupe", "dedupe_key", unique=True, postgresql_where=text("status = 'open'")),
        Index("ix_alerts_org_status", "org_id", "status", "severity"),
    )

    id:               Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id:           Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    project_id:       Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    alert_type:       Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity:         Mapped[str] = mapped_column(String(10), nullable=False)  # critical | warning | info
    status:           Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|acknowledged|resolved
    title:            Mapped[str] = mapped_column(String(200), nullable=False)
    summary:          Mapped[str] = mapped_column(Text, nullable=False)
    subject:          Mapped[str] = mapped_column(String(200), nullable=False)  # 'step:8421', 'agent:support-bot'
    dedupe_key:       Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_json:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detector:         Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at:    Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_at:     Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    acknowledged_at:  Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    acknowledged_by:  Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at:      Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    resolved_by:      Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_emailed_at:  Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at:       Mapped[int] = mapped_column(BigInteger, nullable=False)


class AlertDelivery(Base):
    """
    Durable intent to deliver one alert to one recipient over one channel
    — the transactional-outbox pattern (ADR-0001) reused rather than
    reinvented for email. notifications/sender.py claims rows with
    FOR UPDATE SKIP LOCKED exactly like anchor_worker claims
    anchor_outbox rows.
    """

    __tablename__ = "alert_deliveries"

    id:              Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_id:        Mapped[int] = mapped_column(Integer, ForeignKey("alerts.id"), nullable=False, index=True)
    channel:         Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    recipient:       Mapped[str] = mapped_column(String(320), nullable=False)
    user_id:         Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status:          Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # pending | claimed | sent | failed | dead_letter
    attempts:        Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    claimed_by:      Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claimed_at:      Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_error:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sent_at:         Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at:      Mapped[int] = mapped_column(BigInteger, nullable=False)


class NotificationPreference(Base):
    """Per-user, per-org email preferences. Absence of a row means
    defaults apply (all True except digest-only and info) — a brand-new
    member is correctly opted into critical alerts from the moment they
    join, with no backfill needed."""

    __tablename__ = "notification_preferences"

    user_id:           Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    org_id:            Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), primary_key=True)
    email_critical:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_warning:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_info:        Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_digest_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at:        Mapped[int] = mapped_column(BigInteger, nullable=False)
    # When this user last received a digest email in THIS org — NULL
    # means never. notifications/digest.py checks this against
    # config.alert_digest_interval_seconds to decide who's due; see that
    # module for why this lives per (user, org) rather than globally (a
    # user digest-subscribed to two orgs gets each on its own cadence,
    # not coupled).
    last_digest_sent_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class WatchdogCursor(Base):
    """One row per detector — where the ROLLING tier's sweep last left
    off (see integrity_watchdog/cursor.py), so restart doesn't re-scan
    from the beginning and a full pass over history has a measurable
    duration (`wrapped_at` timestamps consecutive full passes)."""

    __tablename__ = "watchdog_cursor"

    detector:         Mapped[str] = mapped_column(String(40), primary_key=True)
    last_id:          Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    wrapped_at:       Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_run_at:      Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at:       Mapped[int] = mapped_column(BigInteger, nullable=False)


class BatchVerification(Base):
    """Caches the result of detector 4(c) — comparing an anchor_batches
    row's merkle_root against what AgentAuditLogV2.getBatch() actually
    returns on-chain — so a batch verified once is not re-read from the
    chain on every sweep. An anchored batch's on-chain root is immutable;
    there is nothing to gain from asking again, only RPC cost."""

    __tablename__ = "batch_verifications"

    batch_id:                 Mapped[int] = mapped_column(Integer, ForeignKey("anchor_batches.id"), primary_key=True)
    onchain_root_verified_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    onchain_root:              Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    last_rebuilt_at:           Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_result:               Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # ok|mismatch|missing_steps
