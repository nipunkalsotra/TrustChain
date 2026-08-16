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

from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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


class Membership(Base):
    """Which users may act on behalf of which org, and how much authority
    they have. `role` is checked in application code today (owner/admin can
    issue+revoke API keys; member cannot) — not yet enforced by Postgres RLS
    (see db/models.py module note on that being a defense-in-depth layer
    still open, tracked as a known gap rather than silently assumed done)."""

    __tablename__ = "memberships"

    user_id:    Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    org_id:     Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), primary_key=True)
    role:       Mapped[str] = mapped_column(String(20), nullable=False, default="owner")  # owner|admin|member
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


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
