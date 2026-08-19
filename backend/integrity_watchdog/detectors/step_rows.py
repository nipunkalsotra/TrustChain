"""
integrity_watchdog/detectors/step_rows.py — Detector 3 (Phase 3 §6.4):
step row self-consistency.

Recomputes each step's leaf_hash from its OWN currently-stored columns
(run_id, agent_id, action, input_hash, output_hash, step_index, timestamp,
and — for leaf_schema_version=2 rows — agent_code_hash) and compares
against the leaf_hash column. A mismatch means someone edited a
content-bearing column on an already-anchored row without also
recomputing leaf_hash — the naive form of tampering (T3 in the plan's
threat table). CPU-only: no network, no chain, no RPC, so it's cheap
enough to sweep large windows on every cycle.

This does NOT catch an attacker who edits a row AND recomputes leaf_hash
consistently — that passes here by construction (the row is internally
self-consistent). Catching THAT requires comparing the batch's rebuilt
Merkle root against the immutable on-chain root — see merkle_roots.py's
detector 4(b)/(c). The two are complementary, not redundant.
"""

from web3 import Web3

from db.models import Step
import observability
from blockchain.merkle import leaf_hash as leaf_hash_v1
from blockchain.merkle import leaf_hash_v2


def recompute_leaf(step: Step) -> bytes:
    common = dict(
        run_id_hash=bytes(Web3.keccak(text=step.run_id)),
        agent_id_hash=bytes(Web3.keccak(text=step.agent_id)),
        action_hash=bytes(Web3.keccak(text=step.action)),
        input_hash=bytes.fromhex(step.input_hash.removeprefix("0x")),
        output_hash=bytes.fromhex(step.output_hash.removeprefix("0x")),
        step_index=step.step_index,
        timestamp=step.timestamp,
    )
    if step.leaf_schema_version == 2 and step.agent_code_hash:
        return leaf_hash_v2(**common, agent_code_hash=bytes.fromhex(step.agent_code_hash.removeprefix("0x")))
    return leaf_hash_v1(**common)


async def check_steps(steps: list[Step]) -> list[dict]:
    """Returns a list of {"step": Step, "expectedLeaf": str} for every
    row whose stored leaf_hash doesn't match its own recomputed hash —
    caller (integrity_watchdog/main.py) is responsible for resolving
    org_id and calling raise_alert, since that needs a join to `runs`
    this function deliberately doesn't do (keeps it a pure, easily
    unit-testable function over a list of rows already in hand)."""
    mismatches = []
    for step in steps:
        expected = "0x" + recompute_leaf(step).hex()
        if expected.lower() != step.leaf_hash.lower():
            mismatches.append({"step": step, "expectedLeaf": expected})
            observability.INTEGRITY_CHECKS_TOTAL.labels(detector="step_rows", result="mismatch").inc()
        else:
            observability.INTEGRITY_CHECKS_TOTAL.labels(detector="step_rows", result="ok").inc()
    return mismatches
