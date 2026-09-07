"""
evidence/ — publishes a batch's PROOF material (never its content) to a
durable, content-addressed store, so an independent verifier can
reconstruct and check a Merkle proof without depending on TrustChain's
own Postgres staying intact or trustworthy.

- manifest.py — builds the deterministic, no-PII-by-construction manifest
  dict for one AnchorBatch (schema version, root/batch metadata, ordered
  leaf hashes — never raw prompts/outputs/emails/tokens).
- backends/ — pluggable publish-one-manifest implementations (disabled/
  pinata), selected by config.Settings.evidence_publisher_backend.
  Mirrors notifications/backends/ and blockchain/signer.py's identical
  "one Protocol, one factory keyed off a config string" shape (ADR-0008).

See docs/threat-model.md's T2 residual-risk note, which this directly
addresses for the "batch's full leaf set" half of that gap — NOT for the
"raw step content" half, which remains governed by the tenant database
alone unless a separate encrypted-evidence-escrow design is built later
(out of scope here — see that note's own wording).
"""
