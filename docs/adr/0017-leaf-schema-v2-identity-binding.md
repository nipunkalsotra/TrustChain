# 0017 — Leaf schema v2: binding agent identity into the anchored hash

**Status:** Accepted

## Context

`AgentIdentityRegistryV2` proves an agent's *registered* identity was
never silently changed without an on-chain event. It says nothing about
whether a specific *step* was actually produced by that registered
identity — `steps.agent_id` is a plain string column, editable by anyone
with database access, and nothing about the existing Merkle leaf
(`blockchain/merkle.py::leaf_hash`) binds a step to any notion of "which
config produced this." An attacker (or a bug) could relabel a step as
having come from a different agent than it did, and the anchored proof
would not notice — the leaf hashes `agent_id_hash`, but that's exactly
the field being changed, hashed the same way regardless of what it
contains.

## Decision

A new leaf preimage, `leaf_hash_v2`, identical to the original except
for one additional `bytes32` field: `agent_code_hash` — the same
fingerprint `register_agent()`/`_code_hash()` already compute
(`keccak256(json({agentId, model, version, systemPrompt}, sorted)))`.
`steps.leaf_schema_version` (default `1`) records which preimage
produced a given row's `leaf_hash`; `steps.agent_code_hash` is `NULL`
for every row logged by an SDK that predates this. Storing the
fingerprint as an ordinary column would not be enough — an attacker
with database access could edit that column exactly as freely as
`agent_id` today. Putting it **inside the hashed preimage** means an
edit to that column breaks the anchored leaf, the same way an edit to
`output_hash` already does.

This is a new, additively-versioned scheme, not a migration of the old
one: v1 and v2 leaves coexist in the same Merkle tree with zero special
handling (`build_tree`/`build_levels` operate on opaque 32-byte leaves
regardless of preimage), and `AgentAuditLogV2.verifyProof()` takes an
opaque `bytes32` leaf too — the contract never knew either preimage, so
every proof anchored before this shipped verifies exactly as it did
before.

## Alternatives considered

- **Protect `steps.agent_code_hash` with a server-side HMAC instead of
  putting it in the leaf.** Rejected — introduces a secret whose
  compromise silently defeats the check, and breaks the property that
  *anyone* can independently re-derive a leaf from public data (the
  entire reason this system anchors hashes on a public chain rather than
  trusting its own signature over them).
- **Replace `leaf_hash` in place rather than version it.** Would
  invalidate every proof anchored under Phase 1/2 the instant this
  shipped — a real, needless breaking change against data that is
  supposed to be permanently, independently verifiable. Versioning costs
  one `SMALLINT` column and a branch in two places (leaf construction,
  proof verification) in exchange for that guarantee holding.
- **Bind identity by including it in `output_hash`'s own preimage
  instead of a new leaf field.** Rejected — conflates two independently
  meaningful hashes (what the agent said/did vs. which agent said/did
  it) into one, which would make it impossible to reason about either
  in isolation when interpreting a mismatch (e.g. detector 3's evidence
  couldn't distinguish "the output changed" from "the identity changed"
  without redoing the whole computation both ways).

## Consequences

- `POST /steps`' `agent_code_hash` field and `agents/base.py::log_step`'s
  matching parameter are both optional — an SDK/caller that omits it
  gets exactly Phase 2 leaf behavior (v1, no drift check). Nothing about
  existing integrations breaks by not upgrading.
- `GET /steps/{id}/proof` now returns `leafSchemaVersion`/
  `agentCodeHash` (`db/read_model.py::get_step_proof`) so an independent
  verifier knows which preimage to reconstruct — the SDKs' `MerkleProof`/
  `MerkleProofResult` carry the same two fields.
- Python SDK's `_code_hash` and the TypeScript SDK's `codeHash` must
  keep producing byte-identical hashes from identical inputs — a
  divergence here would silently make every drift check a false
  positive for whichever SDK disagrees, the same risk the pre-existing
  `_code_hash` docstring already calls out for registration.
  `sdk/typescript/tests/conformance.test.ts` (added this phase) checks
  this against fixed vectors and **caught a real, pre-existing
  divergence while being written**: Python's `json.dumps` defaults to
  `ensure_ascii=True` (escaping every non-ASCII character to `\uXXXX`)
  and both `backend/blockchain/hashing_utils.py::compute_hash` and the
  Python SDK's `_code_hash` rely on that default; `JSON.stringify` has
  no equivalent mode and left non-ASCII characters literal. Any
  `agentId`/`model`/`version`/`systemPrompt` containing a non-ASCII
  character (an accented name, CJK text, an emoji in a prompt) produced
  silently different hashes between the two SDKs before this was found
  — fixed in `codeHash()` (`pythonEnsureAsciiEscape`), verified by the
  same test that caught it.
