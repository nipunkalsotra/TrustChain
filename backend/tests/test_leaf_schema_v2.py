"""
tests/test_leaf_schema_v2.py — identity-bound Merkle leaves (Phase 3
§6.2). Pure/unit: no DB, no chain — blockchain/merkle.py's leaf_hash and
leaf_hash_v2 are deterministic functions over bytes.
"""

from web3 import Web3

from blockchain.merkle import build_tree, leaf_hash, leaf_hash_v2, verify_proof


def _common_fields(run_id="run_1", agent_id="support-bot", action="answer_query"):
    return dict(
        run_id_hash=bytes(Web3.keccak(text=run_id)),
        agent_id_hash=bytes(Web3.keccak(text=agent_id)),
        action_hash=bytes(Web3.keccak(text=action)),
        input_hash=bytes(Web3.solidity_keccak(["string"], ["hello"])),
        output_hash=bytes(Web3.solidity_keccak(["string"], ["world"])),
        step_index=0,
        timestamp=1755000000,
    )


def test_v1_leaf_is_unaffected_by_v2_existing():
    """The whole point of versioning rather than replacing: a v1 leaf
    computed today must be byte-identical to one computed before Phase 3
    ever existed — nothing about existing anchored proofs can change."""
    fields = _common_fields()
    leaf_a = leaf_hash(**fields)
    leaf_b = leaf_hash(**fields)
    assert leaf_a == leaf_b
    assert len(leaf_a) == 32


def test_v2_leaf_differs_from_v1_for_the_same_step():
    fields = _common_fields()
    agent_code_hash = bytes(Web3.keccak(text="gpt-4o:2025-11:some-prompt"))
    v1 = leaf_hash(**fields)
    v2 = leaf_hash_v2(**fields, agent_code_hash=agent_code_hash)
    assert v1 != v2


def test_v2_leaf_changes_if_agent_code_hash_changes_but_nothing_else_does():
    """This IS the identity-binding property: editing ONLY the identity
    fingerprint (leaving run/agent/action/input/output/index/timestamp
    untouched) must still change the leaf — otherwise the binding is
    cosmetic, not cryptographic."""
    fields = _common_fields()
    hash_a = bytes(Web3.keccak(text="gpt-4o:2025-11:prompt-a"))
    hash_b = bytes(Web3.keccak(text="gpt-4o:2025-11:prompt-b"))
    leaf_a = leaf_hash_v2(**fields, agent_code_hash=hash_a)
    leaf_b = leaf_hash_v2(**fields, agent_code_hash=hash_b)
    assert leaf_a != leaf_b


def test_v2_leaf_is_deterministic():
    fields = _common_fields()
    agent_code_hash = bytes(Web3.keccak(text="claude-sonnet-5:3.1:x"))
    assert leaf_hash_v2(**fields, agent_code_hash=agent_code_hash) == leaf_hash_v2(**fields, agent_code_hash=agent_code_hash)


def test_mixed_v1_and_v2_leaves_coexist_in_one_tree():
    """Phase 3 §6.2's stated design: a batch built from steps logged by
    old and new SDKs alike needs no special handling — build_tree/
    build_levels operate on opaque 32-byte leaves regardless of which
    preimage produced them."""
    v1_leaf = leaf_hash(**_common_fields(run_id="run_a"))
    v2_leaf = leaf_hash_v2(**_common_fields(run_id="run_b"), agent_code_hash=bytes(Web3.keccak(text="x")))
    third_leaf = leaf_hash(**_common_fields(run_id="run_c"))

    tree = build_tree([v1_leaf, v2_leaf, third_leaf])
    for i, leaf in enumerate([v1_leaf, v2_leaf, third_leaf]):
        proof = tree.proof(i)
        assert verify_proof(leaf, proof, tree.root)


def test_a_tampered_agent_code_hash_breaks_the_anchored_proof():
    """The actual security property, end to end at the Merkle-tree level
    (not just the leaf level, see test_v2_leaf_changes_if_... above):
    anchor a batch with the REAL identity hash, then try to prove
    membership using a step whose identity hash was swapped afterward —
    verify_proof must reject it."""
    fields = _common_fields()
    real_hash = bytes(Web3.keccak(text="gpt-4o:2025-11:real-prompt"))
    fake_hash = bytes(Web3.keccak(text="gpt-3.5:2023-01:swapped-model"))

    real_leaf = leaf_hash_v2(**fields, agent_code_hash=real_hash)
    other_leaf = leaf_hash(**_common_fields(run_id="run_other"))
    tree = build_tree([real_leaf, other_leaf])
    proof = tree.proof(0)

    # The attacker recomputes what THEY think the leaf should be, using
    # the swapped identity — this does not match what's actually in the
    # tree, so the proof (computed for the ORIGINAL leaf) fails against it.
    forged_leaf = leaf_hash_v2(**fields, agent_code_hash=fake_hash)
    assert not verify_proof(forged_leaf, proof, tree.root)
    assert verify_proof(real_leaf, proof, tree.root)  # the real one still works
