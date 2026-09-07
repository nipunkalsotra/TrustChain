"""
evidence/manifest.py — the ONLY thing evidence/backends/ ever publishes.

Deliberately narrow input signature: build_manifest takes exactly the
fields already returned by anchor_worker/batch.py::build_batches (run_id
hash, root, step count, ordered step ids, ordered leaf hashes) and
nothing else — there is no parameter this function could even be handed
that would let raw step content (a prompt, an output, an email address, a
token) end up in the published manifest. That's the actual no-PII
guarantee: not a filter that might miss something, a function that was
never given anything to filter in the first place.
"""

import json

_SCHEMA_VERSION = 1


def build_manifest(batch: dict) -> dict:
    """`batch` is one of anchor_worker/batch.py::build_batches's returned
    dicts: {batch_id, run_id, run_id_hash, root_hex, step_count,
    leaf_order, leaf_hashes, outbox_ids}. Returns a plain, JSON-serializable
    dict — deterministic for a given batch (same inputs always produce the
    exact same manifest, key-for-key and value-for-value), which is what
    makes a content-addressed store's own deduplication work correctly
    (re-publishing an identical manifest after a crash-and-retry lands at
    the SAME address, not a new one) and what makes this function safe to
    unit-test for byte-for-byte reproducibility.

    Deliberately does NOT include batch_id or outbox_ids — both are
    TrustChain's own internal primary keys, meaningless (and slightly
    informative about internal row counts) to an external verifier, and
    their presence would make the manifest depend on internal state that
    has nothing to do with the actual cryptographic claim being published."""
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "runIdHash": batch["run_id_hash"],
        "merkleRoot": batch["root_hex"],
        "stepCount": batch["step_count"],
        # Parallel arrays, not a list of {stepId, leafHash} objects — keeps
        # the manifest's own JSON serialization simpler to reproduce
        # byte-for-byte across languages (no dict key ordering to worry
        # about within each entry), and a verifier only ever needs to zip
        # them back together locally if it wants that shape.
        "leafOrder": list(batch["leaf_order"]),
        "leafHashes": list(batch["leaf_hashes"]),
    }


def serialize_manifest(manifest: dict) -> str:
    """Canonical JSON serialization — sorted keys, no extra whitespace —
    so two independently-built manifests for the same batch always
    serialize to the exact same bytes. This is what actually gets
    published (evidence/backends/ publish this string's bytes, not the
    dict), and what a verifier re-hashes/re-parses on the other end."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))
