# Deployment modes: hosted vs. self-hosted

TrustChain ships one real, working deployment mode today: **self-hosted,
single-host, single-organization**. This document describes that mode
precisely (what it is, what you're responsible for), and separately
sketches what a **hosted, multi-organization** mode would require — an
explicit design note for future work, not a description of something
that exists. Conflating the two would misrepresent what a deployer is
actually getting; see [`docs/threat-model.md`](threat-model.md) for how
each mode changes the actual trust boundaries, not just the ops story.

## Self-hosted (what exists today)

**What it is:** you run `docker-compose.yml` (or the images it builds —
`ghcr.io/<owner>/trustchain-backend`, serving `api`/`anchor-worker`/
`indexer` from one image with a different `command:` per service, plus
the two MCP server images — see `docs/release-process.md`'s "Docker
images" section) on infrastructure you control. One deployment, one
Postgres, one Redis, one signing key (or KMS/Vault configuration),
serving however many *projects* your own organization creates under it.
The multi-tenancy data model (ADR-0004, projects/orgs/memberships) still
applies — you can have multiple projects — but there's no separate
"TrustChain the vendor" actor in this mode: you are both the deployer
and the operator, and invariant I7 (tenant isolation) is protecting
*your own* projects from each other, not protecting you from a hosting
provider or protecting other customers from you.

**What you're responsible for, that a hosted mode would otherwise
absorb:**

| Concern | Where it's configured |
|---|---|
| Postgres/Redis provisioning, backup, and restore | `docs/runbooks.md`'s "Restoring the database from a snapshot" — you own the backup schedule and the drill |
| RPC endpoint(s) for the chain you're anchoring to | `MONAD_RPC_URL`/`V2_RPC_URL` + optional `*_RPC_FALLBACK_URLS` (ADR-0009) |
| Signing-key custody | `SIGNER_BACKEND` — `local` (dev only), `aws_kms`/`gcp_kms` (raw key never leaves the KMS), `vault_kv` (ADR-0008) — you choose and you hold the underlying credentials either way |
| Admin-role custody on the V2 contracts | `DEFAULT_ADMIN_ROLE` — a single EOA by default, a Gnosis Safe if you run the handoff (`docs/multisig-admin-handoff.md`, ADR-0012) |
| TLS termination, DNS, and any reverse proxy in front of `api` | Not provided by this repo — `docker-compose.yml` exposes plain HTTP on `:8000` |
| Deploy orchestration | `deploy/canary_rollout.sh` (ADR-0011) — a bake-then-commit-or-automatic-rollback script run over SSH against this same docker-compose model, not a fleet behind a load balancer |
| Monitoring/alerting | Prometheus + Grafana are part of the compose stack (`docker/prometheus/alerts.yml`) but nothing pages *you* unless you wire up Alertmanager (or equivalent) yourself — no such wiring exists in this repo today |

**Honest limitation:** `docs/release-process.md`'s own "Honest
limitation: no real deployment target yet" section is the ground truth
here — this repo's CI has never deployed to a real staging/production
host (no cloud account, no kubeconfig configured against this repo). The
self-hosted path is real, tested code (`deploy/canary_rollout_test.sh`'s
synthetic bake/crash/rollback scenarios, plus a genuine end-to-end run
against this repo's own docker-compose stack — see that doc), but *you*
are the first real host it will ever run against.

## Hosted (design sketch — not built)

A hosted mode — one TrustChain deployment serving multiple, mutually
untrusting *organizations*, run by a vendor neither org otherwise trusts
— is a genuinely different trust shape, not just "the same thing at
larger scale." What would actually need to change, concretely:

- **Per-tenant signing-key isolation, or an explicit shared-key
  tradeoff.** Today's `SIGNER_BACKEND` config is process-wide — one key
  (or one KMS/Vault path) signs every batch/registration for every
  project in the deployment. A hosted mode needs a real answer to
  "does every customer share one signing key (a single point of failure
  across all of them, and a customer can't independently rotate or
  audit access to a key they don't control) or does each get isolated
  signing material (real infrastructure cost per tenant, not built,
  and `blockchain/signer.py`'s `Signer` protocol would need a
  per-project resolution path instead of the current single
  process-wide instance)." Neither answer is implemented; this is the
  single largest gap between today's code and a real hosted offering.
- **A genuinely separate hosting-operator actor in the threat model.**
  Self-hosted has no one to defend a customer *from* except themselves;
  hosted introduces an operator every customer implicitly trusts
  (infrastructure access, the ability to read Postgres directly bypasses
  RLS by Postgres's own semantics — see `docs/threat-model.md`'s T3) but
  that no individual customer chose or can audit. That's a real,
  qualitatively different risk a self-hosted deployer never accepts.
- **Billing and per-org resource ceilings becoming customer-facing, not
  just an internal safety valve.** The gas-spend and LLM-token budget
  ceilings (`organizations.gas_budget_wei`/`token_budget`,
  `db/tenancy.py`) already exist and already default to unlimited (NULL)
  — built as a DoS/cost safety mechanism, not a billing/plan-enforcement
  one. Turning "an org's budget" into "what a customer is actually
  paying for and can see/manage themselves" needs a real
  billing-integration surface that doesn't exist (no Stripe/usage-metering
  code anywhere in this repo).
- **Regulatory/data-residency questions** (which jurisdiction's data
  protection law applies to which customer's data, whether customers can
  require regional data residency) that a single self-hosted deployer
  answers implicitly by choosing where they run their own infrastructure,
  and a hosted vendor would have to answer explicitly, per customer.
- **A support/incident-response surface** distinguishing "your data" from
  "another customer's data" when triaging a shared-infrastructure
  incident — today's `docs/runbooks.md` is written for a single
  operator debugging their own single-tenant-in-practice deployment.

None of this is scheduled work — it's written down here specifically so
"can TrustChain be offered as a hosted product" has an honest answer
("not without this list being addressed") rather than an implicit,
untested assumption that self-hosted code just works the same way at
SaaS scale.
