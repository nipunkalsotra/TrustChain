# Threat model

What TrustChain is actually defending, who it's defending it from, and
which of that is real (built, tested against real infrastructure) versus
still a known gap. Written in the same voice as
[`docs/slo.md`](slo.md)'s "Honest starting point" — this isn't a
compliance-checkbox document, it's what an engineer deciding whether to
trust a deployment should actually read.

## What's actually being protected

TrustChain's core claim is: **a pipeline run's recorded steps —
inputs, outputs, scores — are exactly what happened, verifiably, without
having to trust TrustChain's own database.** Everything in this document
is in service of that one claim staying true, or failing loudly and
specifically when it can't.

Three assets follow from that claim directly:

1. **The anchored audit trail's integrity** — a Merkle root committed
   on-chain must actually correspond to the leaf hashes it claims to,
   and those leaf hashes must actually correspond to real, unmodified
   step content (`agents/base.py::log_step`, ADR-0001/ADR-0002).
2. **Tenant isolation** — no project can read or write another
   project's runs, agents, scores, or keys (invariant I7, ADR-0004/
   ADR-0006). A single-tenant integrity guarantee is worthless if a
   different tenant can silently see or tamper with it.
3. **Signing-key custody** — whoever can produce a valid signature for
   `anchorBatch()`/`updateScore()`/agent registration calls can write
   data this system calls immutable. Key compromise doesn't just leak
   data, it forges the thing the whole product exists to make
   trustworthy.

## Actors

| Actor | Capability | Trust level |
|---|---|---|
| **Unauthenticated caller** | Hits any endpoint with no credential | None — every write and most reads require auth |
| **A registered tenant (project)** | Valid API key or session JWT scoped to their own project | Trusted for their own data only |
| **A malicious/compromised tenant** | Same as above, but actively trying to read/write outside their project | Same credential, adversarial intent |
| **An operator/insider** | Direct DB access, deploy access, or a compromised CI/CD credential | High — this is the hardest actor to defend against and the one this doc is least complete against (see Known gaps) |
| **A third party on the public chain** | Anyone who can read on-chain state or submit transactions to the deployed contracts — doesn't need any TrustChain credential at all | Public by construction; the whole point of anchoring is that this actor can independently verify without trusting TrustChain |
| **A compromised dependency** | A malicious/compromised third-party package, RPC provider, or LLM API | Varies — see Known gaps |

## Threats and mitigations, by category

### T1 — Tenant isolation bypass

**Threat:** a malicious/compromised tenant reads or writes another
tenant's runs, agents, scores, or API keys by exploiting a missed
`project_id` filter in application code.

**Mitigation:** enforced at two independent layers (ADR-0006) —
application-level `WHERE project_id = ...` filtering AND Postgres
Row-Level Security under a separate, non-superuser `trustchain_api`
role, so a single missed filter in a future endpoint doesn't silently
become a cross-tenant leak. `tests/test_row_level_security.py`
exercises the RLS layer directly against a real Postgres connection
under that restricted role — not the superuser connection the rest of
the suite uses — specifically so a bug in the *application*-layer
filter would still be caught by the *database*-layer one.

### T2 — Forged or tampered audit trail

**Threat:** a step's recorded content is altered after the fact, or a
Merkle proof is fabricated, without it being detectable by an
independent verifier.

**Mitigation:** the transactional outbox (ADR-0001) makes "recorded"
and "durably queued for anchoring" atomic — no window where a step
exists in the app's database but was never committed to being
anchored. `leaf_order` is persisted per batch, not re-derived (ADR-0002),
so a proof can always be reconstructed exactly regardless of later
table state. Verification (`GET /steps/{id}/proof`,
`verify_proof`/`verify_proof_onchain` in both SDKs) recomputes the
Merkle path from the leaf and checks it against the on-chain root
directly — it does not ask TrustChain's API "is this valid," it asks
the chain.

**Residual risk:** this protects the anchored *hash*, not the
plaintext content a caller separately stored. If the only copy of the
raw input/output text lives in TrustChain's own database, an operator
with DB access could alter it and the hash would no longer match on
re-verification (an anchor mismatch is itself a strong integrity
signal) — but nothing stops that same operator from also serving
altered text via the read API, since the API is the thing computing
"does this match" for any caller who doesn't independently hold their
own copy of the original content. Independent verification is only as
strong as the caller's own retained copy of what they expect the hash
to be.

### T3 — Insider/operator misuse

**Threat:** someone with legitimate operational access (DB access,
deploy credentials, a compromised CI/CD pipeline) takes an
authority-affecting action — issuing themselves an API key, revoking
another org's agent, mutating anchor state directly in Postgres —
without it being independently visible.

**Mitigation:** admin-authority actions on the platform side (key
issuance/revocation, and equivalents) are themselves audited
(`db/models.py`'s `AuditEvent`, `main.py::audit_log_admin_action`) —
best-effort, logging failure never blocks the action itself, but a
logging *failure* is a different, narrower risk than no logging at
all. On the contract side, `DEFAULT_ADMIN_ROLE` (rare, ideally a
multisig) is separated from `ANCHOR_ROLE`/`REGISTRAR_ROLE` (routine,
held by a hot key) specifically so a compromised hot key can submit
batches/register agents but cannot grant itself broader authority,
pause contracts, or redirect admin power (ADR-0012,
`docs/multisig-admin-handoff.md`).

**Residual risk:** `DeployV2.s.sol`'s default (no `SAFE_ADDRESS` set)
still grants `DEFAULT_ADMIN_ROLE` to a single EOA — ADR-0012 fixed the
common failure mode (forgetting to run the handoff script at all) but
a deployment that never sets `SAFE_ADDRESS` is knowingly running with
that risk. Direct Postgres access (a superuser DB credential, or
physical/cloud-provider access to the database) bypasses RLS entirely
by Postgres's own semantics (`anchor-worker`/`indexer` already connect
this way, by design — see `db/engine.py`'s module docstring) — nothing
in this system detects or prevents a superuser-level actor from
editing rows directly. This is a real, currently-unmitigated gap, not
an oversight papered over: mitigating it further (audit logging at the
Postgres level, e.g. `pgaudit`, or read-only replicas for anything
that doesn't need write access) is genuinely future work.

### T4 — Signing-key compromise

**Threat:** the anchor worker's or identity registrar's signing key is
exfiltrated, letting an attacker submit arbitrary `anchorBatch()`/
`updateScore()`/agent-registration transactions as if they were
legitimate.

**Mitigation:** pluggable signer backends (ADR-0008) — `LocalKeySigner`
(dev only, raw key in env), `AwsKmsSigner`/`GcpKmsSigner` (the raw key
never leaves the cloud KMS; TrustChain only ever sees signed output),
`VaultKvSigner` (centrally managed custody via HashiCorp Vault, a
different and weaker guarantee than the KMS backends — see that
class's own docstring for the honest distinction, not oversold as
equivalent). `ANCHOR_ROLE`/`REGISTRAR_ROLE` are the only authority a
compromised hot key would actually hold (see T3) — it can anchor
false-but-correctly-signed batches, it cannot grant itself admin power
or redirect the contracts entirely.

**Residual risk:** a compromised `ANCHOR_ROLE` key CAN submit a
correctly-signed but semantically false batch (a Merkle root that
doesn't correspond to any real `steps` this process actually recorded)
— nothing on-chain distinguishes "the legitimate anchor worker
anchored real data" from "someone holding that role's key anchored
fabricated data." This is the sharpest edge of the whole trust model:
anchoring proves *what got committed*, not *that the committer was
honest about where it came from*. Detecting this class of attack would
need something outside this system's current scope (e.g. independent
auditors reconciling anchored roots against the off-chain record they
claim to summarize) — not built today.

### T5 — RPC/chain-layer disruption

**Threat:** a malicious or merely unreliable RPC endpoint causes
anchoring, indexing, or health checks to fail, hang, or (worse) silently
degrade without anyone noticing.

**Mitigation:** per-endpoint circuit breaker + multi-endpoint fallback
(ADR-0009) so one dead RPC endpoint doesn't take the whole pipeline
down, layered under per-call retry-with-jitter + an explicit timeout
(that ADR's F13 addendum) so a single transient blip doesn't even reach
the breaker's failure count. `RPC_CALL_FAILURES_TOTAL`/circuit-breaker
state are both observable (`/metrics`, `RpcCircuitBreakerOpen`/
`RpcCallFailuresElevated` alerts — `docker/prometheus/alerts.yml`).

**Residual risk:** none of this defends against a *malicious* RPC
endpoint returning plausible-looking but false data (a fake receipt, a
fake `chainId`) rather than simply failing — the resilience layer's
threat model is availability/reliability, not RPC-provider honesty.
Configuring a fallback URL you don't independently trust doesn't help;
it just gives a dishonest endpoint a chance to be the one that answers.

### T6 — Credential-stuffing / brute-force login

**Threat:** an attacker tries many password guesses against real
accounts, or bursts requests to exhaust resources.

**Mitigation:** per-IP and per-account login backoff plus a real,
Redis-backed token-bucket rate limiter on write paths (per-project) and
read paths (per-project) and unauthenticated/pre-auth surfaces (per-IP)
— `backend/rate_limit.py`, `docker/prometheus/alerts.yml`'s
`RateLimitRejectionsSpike` (visibility, not itself a verdict — could be
legitimate traffic or an attack, see that alert's own description).
Optional Have I Been Pwned breach-password check at signup
(`auth_pwned.py`, off by default — see that module for why, on by
default it would reject some of this very test suite's own fixture
passwords, which are themselves real breach hits) lets a deployment
reject known-compromised passwords outright rather than only detecting
abuse after the fact.

### T7 — Sensitive data leakage via error responses or logs

**Threat:** an internal error message (a DB connection string with
credentials, an RPC URL with an embedded API key, a stack trace)
reaches an unauthenticated caller or an insufficiently access-controlled
log sink.

**Mitigation:** `GET /ready` (no auth — an orchestrator's health-checker
doesn't carry a bearer token) logs the real exception server-side and
returns only a boolean per check, never the raw exception text (fixed
as a real, found leak — see `main.py::ready`'s own docstring for the
before/after). A static, AST-based CI check
(`backend/scripts/check_anchor_payload_pii.py`) catches PII-shaped
literals a developer might accidentally hard-code into an anchor
payload builder; a separate runtime detector
(`backend/pii_patterns.py`) flags (never mutates or rejects — mutating
what gets hashed would silently break independent proof verification)
PII-shaped *caller-supplied* content for operator visibility.

### T8 — Denial of service via resource exhaustion

**Threat:** a caller (or a bug) drives unbounded LLM spend, unbounded
gas spend, or unbounded request volume against one tenant's own budget
or the shared infrastructure.

**Mitigation:** hard per-org ceilings on both gas spend
(`organizations.gas_budget_wei`/`gas_spent_wei`) and LLM token spend
(`organizations.token_budget`/`tokens_spent`) — both nullable (NULL =
unlimited, the safe default for existing orgs), both updated via an
atomic `UPDATE ... SET x = x + :delta` rather than read-then-write (see
`db/tenancy.py`), both circuit-breaking (a ceiling hit means further
spend is REJECTED before it happens, not merely alerted on after).
Task string length is bounded (`RunAgentRequest.task`, 10,000 chars) —
that string feeds 4 separate LLM calls in the pipeline, so an unbounded
task multiplies cost 4x, not 1x. Rate limiting (T6) covers request
*volume* separately from spend.

## Hosted vs. self-hosted: what changes

See [`deployment-modes.md`](deployment-modes.md) for the full writeup —
summarized here because it changes several actors' trust level above:

- **Self-hosted**, the only mode this project actually ships today
  (ADR-0011's single docker-compose host): the deploying org IS the
  operator (T3) and controls signing-key custody (T4) directly — there
  is no separate "TrustChain the company" actor to reason about, and no
  cross-customer blast radius, since there's only one tenant's
  infrastructure per deployment even though the *data model* supports
  multiple projects within it.
- **Hosted** (a single shared deployment serving multiple, mutually
  untrusting *organizations* — not yet built, not yet a real product):
  T1 (tenant isolation) becomes the load-bearing defense between
  customers who don't know each other exist, T3 (insider/operator) gains
  a genuinely new actor (the hosting operator, who is trusted by every
  customer simultaneously and by none of them individually), and T4
  (signing-key custody) needs an answer for whether one shared key signs
  for everyone (a single point of failure across all customers) or each
  tenant gets isolated signing material (real infrastructure cost, not
  built).

## Known gaps (honest, not exhaustive)

- **No mitigation for T3's "superuser DB access" or T4's "correctly-signed
  but dishonest batch"** — both are named above as genuinely unsolved,
  not solved-but-undocumented.
- **No independent, third-party security audit has been performed.**
  This document is an internal engineering assessment, not a
  substitute for one.
- **No formal incident-response runbook for a confirmed key
  compromise** beyond the mechanical steps in
  `docs/runbooks.md`'s "Rotating the anchor worker's signing key" and
  "Pausing contracts in an emergency" — those cover *how* to rotate/pause,
  not the surrounding decision process (who declares an incident, who
  has authority to pause, customer notification) a real production
  deployment would need.
- **Dependency supply-chain risk** (a compromised PyPI/npm package, a
  compromised base Docker image) has partial coverage — `pip-audit`/
  `npm audit`/Trivy image scanning run in CI (`.github/workflows/test.yml`,
  several deliberately non-blocking against pre-existing findings — see
  that file's own comments), SBOM generation + keyless Sigstore signing
  exist for the API image, and every image `release.yml` actually
  publishes to GHCR is itself keyless-signed by digest (cosign, same
  OIDC/Fulcio/Rekor mechanism as the SBOM) plus carries an explicit SLSA
  provenance attestation — but there's no policy for how fast a real
  scanner finding gets triaged/patched, and `npm audit`'s current
  findings are explicitly left unresolved pending a deliberate, reviewed
  Next.js upgrade rather than fixed silently. Signing proves *what got
  published came from this repo's own CI*; it doesn't vet the
  dependencies that went into building it in the first place.
