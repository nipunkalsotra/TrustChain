"""
integrity_watchdog/detectors/merkle_roots.py — Detector 4 (Phase 3 §6.5):
the cryptographic backstop, and the only detector that is genuinely
unforgeable by someone with database access alone.

Three checks, over one confirmed AnchorBatch and its `leaf_order`:

  4(a) MISSING STEPS — every step_id in leaf_order must have a
       corresponding `steps` row. A missing one means a row was deleted
       after anchoring (T4 in the plan's threat table).

  4(b) BATCH ROOT MISMATCH — rebuilding the Merkle tree from the batch's
       CURRENT leaves (steps.leaf_hash, in leaf_order) must reproduce
       anchor_batches.merkle_root. This catches the sophisticated form of
       T3 that step_rows.py's detector 3 cannot: an attacker who edited a
       step's content AND recomputed that row's own leaf_hash
       consistently passes detector 3 (the row is internally
       self-consistent) but still changes what the WHOLE TREE hashes to,
       because the edited leaf is a different 32 bytes than the one
       originally included.

  4(c) ON-CHAIN ROOT MISMATCH — comparing anchor_batches.merkle_root
       itself against AgentAuditLogV2.getBatch(anchorId).merkleRoot,
       real chain data. This is what makes the whole scheme trustworthy:
       (a) and (b) only prove internal consistency of TrustChain's own
       database — an attacker with FULL Postgres access could edit the
       steps AND the anchor_batches.merkle_root row together and have
       them agree with each other. They cannot also rewrite what's on an
       immutable chain. Gated by
       config.watchdog_onchain_root_check_enabled and run at a lower
       cadence than (a)/(b) (main.py's tiering) since it's the only check
       here that costs an RPC call — and once a batch's on-chain root is
       verified, batch_verifications caches that result: an immutable
       fact doesn't need re-reading.
"""

import asyncio

from db.models import AnchorBatch, Step
import observability
from blockchain.merkle import build_tree


def rebuild_root(steps_by_id: dict[int, Step], leaf_order: list[int]) -> tuple[str, list[int]]:
    """Returns (rebuilt_root_hex, missing_step_ids). Rebuilds over
    whatever leaves ARE present — a missing step is reported separately
    (4a) rather than silently making the rebuilt root incomparable; the
    root comparison (4b) is only meaningful when nothing is missing, so
    callers should treat a non-empty missing list as its own finding
    first."""
    missing = [sid for sid in leaf_order if sid not in steps_by_id]
    present_order = [sid for sid in leaf_order if sid in steps_by_id]
    if not present_order:
        return "", missing
    leaves = [bytes.fromhex(steps_by_id[sid].leaf_hash.removeprefix("0x")) for sid in present_order]
    tree = build_tree(leaves)
    return tree.root_hex, missing


async def check_onchain_root(batch: AnchorBatch) -> tuple[bool, str]:
    """4(c) — returns (matches, onchain_root_hex). Raises if the RPC call
    itself fails (caller decides how to treat that — see
    integrity_watchdog/main.py, which counts it as a soft failure, not a
    tamper finding: an unreachable RPC node says nothing about whether
    the data was tampered with)."""
    from indexer.chain import get_audit_log_contract

    contract = get_audit_log_contract()
    record = await asyncio.to_thread(lambda: contract.functions.getBatch(batch.onchain_anchor_id).call())
    # BatchRecord: (runIdHash, merkleRoot, stepCount, timestamp, anchoredBy)
    onchain_root = "0x" + record[1].hex()
    observability.INTEGRITY_CHECKS_TOTAL.labels(
        detector="merkle_roots_onchain", result="ok" if onchain_root.lower() == batch.merkle_root.lower() else "mismatch",
    ).inc()
    return onchain_root.lower() == batch.merkle_root.lower(), onchain_root
