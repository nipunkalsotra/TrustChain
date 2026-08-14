# 0008 — Pluggable signer backends (local / KMS / Vault)

**Status:** Accepted

## Context

Every on-chain write (anchoring, score updates, agent registration)
needs a private key to sign transactions. A raw key in an environment
variable is fine for local Anvil/testnet development and unacceptable
for a production key with real value behind it — but the actual signing
call sites (`anchor_worker/submit.py`, `blockchain/score_writer.py`,
`blockchain/identity_writer.py`) shouldn't need to know or care *how*
a signature gets produced, only that it does.

## Decision

A `Signer` protocol (`blockchain/signer.py`) — `address` property +
`sign_transaction(tx)` — with four implementations, selected at runtime
via `SIGNER_BACKEND` config:

- **`LocalKeySigner`** — raw key in process memory. Default; fine for
  Anvil/testnet.
- **`AwsKmsSigner`** / **`GcpKmsSigner`** — the private key never
  leaves the cloud KMS; only a 32-byte digest goes in, a DER-encoded
  ECDSA signature comes out. Turning that into a valid Ethereum
  signed transaction (low-s canonicalization, recovery-id brute force,
  v-encoding per transaction type) is real, non-trivial logic, shared
  between both cloud backends (`_sign_via_kms_digest`) and verified by
  signing the SAME transaction with `LocalKeySigner` and each KMS
  signer and asserting byte-identical raw transaction output.
- **`VaultKvSigner`** — fetches the raw key from HashiCorp Vault's KV
  v2 engine once at construction, then delegates to `LocalKeySigner`
  from there. **Explicitly weaker** than the KMS backends and disclosed
  as such in its own docstring: Vault's OSS Transit engine (the actual
  "never expose the key" equivalent) doesn't support secp256k1 as of
  any current release, so this is Vault-backed *key custody*
  (centralized storage, access control, audit log, rotation without a
  redeploy), not Vault-backed *signing* — the raw key does sit in this
  process's memory after the initial fetch.

Every backend requiring an optional dependency (`boto3`,
`google-cloud-kms`, `hvac`) raises a clear `ImportError` naming the
package to install, rather than failing with an unrelated
`ModuleNotFoundError` deep in a traceback, when constructed without an
injected test client and the package isn't installed.

## Alternatives considered

- **KMS-only, no local-key option.** Rejected: local dev against Anvil
  needs a fast, dependency-free signer; requiring real cloud credentials
  to run tests/local dev would be a significant, unjustified barrier.
- **Present `VaultKvSigner` as equivalent to the KMS backends** (glossing
  over the secp256k1/Transit limitation). Rejected as dishonest — the
  actual security property differs, and a reader choosing a backend
  needs to know that before, not after, relying on it.
- **A single "cloud KMS" abstraction covering AWS/GCP/Vault uniformly.**
  Rejected: the three have genuinely different trust models (two never
  expose the key at all, one does after an initial fetch) — flattening
  that into one interface would hide a real, relevant distinction from
  whoever configures `SIGNER_BACKEND`.

## Consequences

- Switching signing backends in production is a config change
  (`SIGNER_BACKEND` + the backend's own required settings), never a
  code change at any call site — `get_signer()`
  (`anchor_worker/chain.py`) is the only place that branches on backend
  choice.
- `boto3`/`google-cloud-kms`/`hvac` stay out of the base
  `requirements.txt` — installed only if the corresponding backend is
  actually used, keeping the default dependency footprint small.
- Anyone adding a fifth backend needs to either fit the existing
  digest-in/signature-out shape (reusable via `_sign_via_kms_digest`) or
  document why theirs is a genuinely different trust model, matching
  `VaultKvSigner`'s precedent of disclosing the difference rather than
  presenting a weaker guarantee as equivalent to a stronger one.
