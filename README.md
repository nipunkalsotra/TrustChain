<div align="center">

<img src="assets/readme/trustchain-hero.gif" alt="TrustChain animated introduction: agent steps flow through a Merkle tree into a verified root, cycling through record, fingerprint, anchor, and verify" width="100%">

# Your AI did the work. Now show the evidence.

**Run agents. Verify identities. Follow every action to its proof.**

TrustChain is an audit and verification platform for AI agents. Run its four-agent research pipeline, or instrument an agent you already operate with the Python or TypeScript SDK. Every recorded step enters a durable audit trail; an asynchronous worker anchors Merkle roots on-chain so that evidence can be checked independently of the application’s database.

[Watch the demo](https://drive.google.com/file/d/1VwDXoUjVy7EhIUsaaVJdGt2BAXk2b_Uh/view?usp=sharing) · [See it working](#see-it-working) · [How it works](#how-it-works) · [Architecture](#architecture) · [Use the SDK](#instrument-your-own-agent) · [Run locally](#run-locally) · [Verification](#verified-against-the-running-system)

</div>

---

## The idea in 30 seconds

An AI answer alone does not tell you which configuration produced it, what the agent did along the way, or whether the audit record changed afterward.

TrustChain connects those questions:

| Question | What you can inspect |
|---|---|
| **What happened?** | A persisted run and its ordered agent activity |
| **Which agent configuration was involved?** | A registered fingerprint of model, version, and system prompt; identity-bound steps when that fingerprint is supplied |
| **Has the evidence changed?** | Content hashes, Merkle proofs, and anchored roots |
| **What is confirmed?** | Per-step anchor state, transaction hashes, and block references |
| **What needs attention?** | Integrity findings with evidence, investigation notes, and an alert lifecycle |

**Two ways in. One evidence system.**

- **Run the built-in pipeline:** researcher → validator → scorer → reporter, with live browser updates, a final report, and agent scores.
- **Audit your own agent:** register its configuration and log activity from your existing application through an SDK. TrustChain does not need to host that agent.

A proof establishes the integrity of recorded evidence. A trust score is a pipeline evaluation. Neither guarantees that an AI-generated statement is true.

---

## See it working

<div align="center">

<a href="https://drive.google.com/file/d/1VwDXoUjVy7EhIUsaaVJdGt2BAXk2b_Uh/view?usp=sharing"><img src="assets/readme/completed-run.png" alt="Watch the full TrustChain demo: a real research run, blockchain proofs, SDK integration, and integrity alerts" width="900"></a>

**[▶ Watch the full end-to-end demo · 5 min 10 sec](https://drive.google.com/file/d/1VwDXoUjVy7EhIUsaaVJdGt2BAXk2b_Uh/view?usp=sharing)**

<sub>Silent walkthrough with short instructional callouts: create a project → run agents → verify evidence → connect the SDK → investigate alerts → manage access.</sub>

</div>

These are excerpts and screenshots from the **actual running application**, not UI mockups or fixture dashboards. The recorded session used real Groq and Tavily calls, real PostgreSQL/Redis services, and V2 contracts on local Anvil chain `31337`.

<table>
<tr>
<td width="50%" align="center">

**The result → the scores**

<img src="assets/readme/run-to-evidence.gif" alt="Actual recorded workflow moving from a completed research report to the four agents’ trust scores" width="100%">

<sub>One task becomes a report, a score, and a persistent activity trail.</sub>

</td>
<td width="50%" align="center">

**The step → the proof**

<img src="assets/readme/proof-in-action.gif" alt="Actual verification studio retrieving a confirmed Merkle proof and opening run integrity verification" width="100%">

<sub>Inspect a leaf, root, transaction, and block; then verify the run.</sub>

</td>
</tr>
</table>

### A workspace built around evidence

![Real TrustChain overview with a completed pipeline, SDK activity, registered agent, and confirmed batches](assets/readme/overview.png)

The same project brings together runs, registered agents, scores, the audit trail, proofs, chain anchors, alerts, API keys, and team settings.

<details>
<summary><b>Open the product gallery</b> — real reports, scores, identities, and findings</summary>
<br>

| Completed workflow | Agent performance |
|---|---|
| ![Completed four-agent workflow with persisted steps and final report](assets/readme/completed-run.png) | ![Four actual agent scores from the completed pipeline](assets/readme/trust-scores.png) |

| Registered identity | Investigation evidence |
|---|---|
| ![Real registered SDK agent with its fingerprint and a successful identity check](assets/readme/agent-identity.png) | ![Real identity-drift alert with expected and presented configuration hashes and an investigation note](assets/readme/integrity-alert.png) |

![Real persisted audit entries with anchor states and evidence links](assets/readme/audit-trail.png)

</details>

The demonstrated pipeline produced **10 confirmed audit steps**. Email verification used the console email backend; the recording does not establish external email delivery or public-chain deployment. The recording used `openai/gpt-oss-20b`; the default model and verification suites used `openai/gpt-oss-120b`. See the [validation report](docs/completion-report-2026-11-11.md) and [media provenance](assets/readme/media-provenance.json).

---

## How it works

### 01 / Execute a task

| Agent | Responsibility | Observable result |
|---|---|---|
| **Researcher** | Search the web through MCP and synthesize findings | Research actions and cited source material |
| **Validator** | Extract claims, call the fact-check tool, and assess the research | A validation report and recorded verification activity |
| **Scorer** | Assign structured agent scores and write them through V2 contracts | Indexed agent performance and score history |
| **Reporter** | Assemble research, validation, and scoring into the final report | A saved result that can be reopened |

The frontend follows the run through a signed SSE stream. Redis carries run events across API processes; PostgreSQL persists the run and its steps. Live updates and persisted results serve different purposes, so reopening a run does not depend on keeping its original browser stream alive.

### 02 / Record before anchoring

<img src="assets/readme/evidence-flow.svg" alt="Evidence lifecycle: hash an action, atomically commit its step and outbox row, build a Merkle tree, confirm a root on-chain, and independently verify the proof; local and on-chain checks are distinguished" width="100%">

The important boundary is the database transaction: the step and its `anchor_outbox` row commit together. The anchor worker later claims pending work, builds a Merkle batch, and submits its root. A chain outage can leave evidence pending without losing the durable intent to anchor it.

**Recorded and confirmed are separate states.** A successful database write does not imply that its chain transaction has already confirmed. The UI exposes that distinction.

### 03 / Verify independently

![The real verification studio showing a confirmed step proof, block reference, Merkle root, and leaf hash](assets/readme/proof.png)

| Verification | What it answers | What it relies on |
|---|---|---|
| **Content check** | Does the supplied text match the recorded input/output hash? | Candidate content plus recorded hash evidence |
| **Local Merkle check** | Does this leaf and sibling path reconstruct the supplied root? | The proof’s supplied root; no network for the calculation |
| **On-chain check** | Does the configured contract verify this proof against anchored evidence? | The chain and contract you independently select |
| **Run integrity check** | Do this run’s recorded steps and available anchored evidence remain consistent? | The applicable integrity checks for that run |

The SDKs support both local proof computation and direct on-chain verification. A local match against a root returned by an API is useful; reading the configured contract directly provides a stronger check on that root’s provenance.

---

## Architecture

<img src="assets/readme/architecture.svg" alt="TrustChain architecture: workspace and SDK clients call a scoped FastAPI control plane; LangGraph records steps and outbox entries in PostgreSQL; Redis carries live events; an anchor worker writes Merkle roots to V2 contracts; an indexer rebuilds read models; a watchdog produces integrity findings" width="100%">

The diagram shows the primary paths. The [architecture document](docs/architecture.md) and [decision records](docs/adr/README.md) describe the full implementation and its tradeoffs.

| Component | Responsibility | Source |
|---|---|---|
| **Next.js workspace** | Run orchestration UI, evidence inspection, organizations, team access, and keys | [frontend/](frontend/) |
| **FastAPI control plane** | Sessions, scoped API keys, permissions, run lifecycle, SDK ingest, verification APIs | [backend/main.py](backend/main.py) |
| **LangGraph pipeline** | Research → validation → scoring → reporting | [backend/agents/](backend/agents/) |
| **MCP tool servers** | Search/fact-check tools and blockchain tool integration | [mcp_servers/](mcp_servers/) |
| **PostgreSQL** | Runs, steps, durable outbox, batches, tenancy, alerts, read models | [models](backend/db/models.py) |
| **Redis** | Run-event transport, rate limits, and coordination | [run events](backend/run_events.py) |
| **Anchor worker** | Claim work, batch leaves, submit roots, track confirmation | [backend/anchor_worker/](backend/anchor_worker/) |
| **Chain indexer** | Consume contract events and reconcile derived application state | [backend/indexer/](backend/indexer/) |
| **Integrity watchdog** | Recheck evidence, detect liveness problems, and drain alert delivery | [backend/integrity_watchdog/](backend/integrity_watchdog/) |
| **V2 contracts** | Audit roots, agent scores, agent identities, deployment resolution | [contracts/src/v2/](contracts/src/v2/) |
| **SDKs + CLI** | Instrument external agents and inspect the same evidence system | [sdk/](sdk/) |

<details>
<summary><b>Why the storage and chain paths are separate</b></summary>
<br>

- A **transactional outbox** makes recording a step and recording its anchoring intent one atomic write. [ADR 0001](docs/adr/0001-transactional-outbox-for-anchoring.md)
- **Merkle batches** anchor many leaves under one root instead of issuing one chain transaction per step. [ADR 0002](docs/adr/0002-merkle-batching-not-per-step-anchoring.md)
- **Read models** derive scores and identities from contract events; the indexer also reconciles anchoring crash windows.
- **Signer coordination** serializes nonce allocation through confirmation across API identity writes, score writes, and batch submissions. [Transaction lock](backend/blockchain/transaction_lock.py)
- **V2 deployment resolution** keeps the configured contract generation explicit. The V1 helpers remain legacy paths, not the primary evidence-write pipeline. [ADR 0003](docs/adr/0003-v1-to-v2-contract-migration-strategy.md)

</details>

---

## When an identity changes

<img src="assets/readme/identity-flow.svg" alt="Identity workflow: register a configuration fingerprint, compare the fingerprint attached to SDK activity, investigate a mismatch, then acknowledge, resolve, or reopen the finding" width="100%">

A model, version, and system prompt define a configuration fingerprint. The SDK computes that fingerprint client-side; the raw system prompt is not sent as the registration payload. When activity includes a fingerprint, the backend can compare it with the registered identity and raise a finding on mismatch.

![Recorded identity verification followed by a genuine SDK identity-drift finding in the alert inbox](assets/readme/identity-to-alert.gif)

That is the real investigation flow: a changed fingerprint was supplied through the SDK, the backend produced a finding, and the UI exposed its evidence for review and resolution.

| Signal | Detection path |
|---|---|
| **A different fingerprint accompanies a step** | Synchronous identity-drift check on SDK ingest |
| **An identity is updated or revoked** | Indexed contract events produce identity findings |
| **A stored step no longer matches its leaf hash** | Watchdog row verification |
| **Leaves, a batch root, or anchored evidence diverge** | Merkle reconstruction and configured chain checks |
| **Anchoring stalls or work is dead-lettered** | Outbox liveness checks |

Findings support acknowledgment, resolution notes, reopening, and eligible notification delivery. An alert is evidence for investigation; an identity update may be intentional and still deserve review.

---

## Instrument your own agent

You can keep your agent in its existing application. TrustChain’s instrumentation surface is separate from its built-in pipeline.

First create a verified account, open **API keys**, and issue a project-scoped key. Agent registration needs `agents:register`, logging needs `logs:write`, and evidence reads need the applicable read scopes. Scope details live in the [SDK documentation](sdk/python/README.md).

### Python

Install from this checkout:

```bash
python -m pip install -e ./sdk/python
```

```python
import os
from trustchain_sdk import TrustChain

with TrustChain(
    api_key=os.environ["TRUSTCHAIN_API_KEY"],
    base_url="http://localhost:8000",
    on_error="raise",
) as tc:
    tc.register_agent(
        agent_id="support-bot",
        model="your-model",
        version="1.0.0",
        system_prompt="Answer support questions carefully.",
    )

    receipt = tc.log_and_wait(
        agent_id="support-bot",
        action="answer_customer",
        input="How can an audit log be verified?",
        output="Reconstruct its Merkle proof and check the anchored root.",
    )
    print(receipt.step_id, receipt.anchor_status)
```

The receipt confirms ingestion. Poll for the proof after anchoring; a newly recorded step can still be pending.

### TypeScript

Build the local package and install it into your application using its checkout path:

```bash
npm --prefix sdk/typescript ci
npm --prefix sdk/typescript run build
# In your application: npm install /absolute/path/to/TrustChain/sdk/typescript
```

```ts
import { TrustChain } from "trustchain-sdk";

const tc = new TrustChain(process.env.TRUSTCHAIN_API_KEY!, {
  baseUrl: "http://localhost:8000",
  onError: "raise",
});

await tc.registerAgent({
  agentId: "support-bot",
  model: "your-model",
  version: "1.0.0",
  systemPrompt: "Answer support questions carefully.",
});

const receipt = await tc.logAndWait({
  agentId: "support-bot",
  action: "answer_customer",
  input: "How can an audit log be verified?",
  output: "Reconstruct its Merkle proof and check the anchored root.",
});

console.log(receipt.stepId, receipt.anchorStatus);
await tc.close();
```

Both SDKs also provide non-blocking logging, decorators/wrappers, optional LangChain integration, typed API errors, local proof checks, and optional direct on-chain verification. Their default instrumentation behavior warns and fails open; these examples opt into exceptions so integration failures are visible.

[Python SDK guide](sdk/python/README.md) · [TypeScript SDK guide](sdk/typescript/README.md) · [CLI guide](sdk/python-cli/README.md) · [Runnable support-agent example](examples/support_agent/README.md)

---

## Run locally

**Prerequisites:** Git with contract submodules, Docker/Compose running, Python with a virtual environment, Node.js/npm, and Foundry (`forge`). CI runtime versions are defined in the [test workflow](.github/workflows/test.yml); the latest local validation used Python 3.14 and Node 26.

```bash
git clone --recurse-submodules https://github.com/nipunkalsotra/TrustChain.git
cd TrustChain

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci

cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Set these values in `backend/.env` before starting:

| Variable | Local purpose |
|---|---|
| `GROQ_API_KEY` | Real model calls; the built-in pipeline requires a configured provider |
| `GROQ_MODEL` | Model selection; defaults to `openai/gpt-oss-120b` and also drives agent identity hashing |
| `TAVILY_API_KEY` | Web search and fact-check tools |
| `JWT_SECRET` | A strong generated session-signing secret |
| `COOKIE_SECURE=false` | Local HTTP browser sessions; use secure cookies for HTTPS deployments |
| `FRONTEND_URL=http://localhost:3000` | Browser origin and email-link base |
| `EMAIL_BACKEND=console` | Capture verification/invitation/reset emails locally |

Generate the signing secret locally:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Start the application:

```bash
./start.sh
```

The launcher starts PostgreSQL, Redis, and Anvil through Compose; applies migrations; deploys local V2 contracts when needed; and starts the API, MCP servers, anchor worker, indexer, watchdog, and frontend. It uses Anvil’s public development key for local V2 writes. A real wallet key is not needed for this local V2 workflow; legacy V1 features have separate configuration.

| Open | Address |
|---|---|
| **Workspace** | <http://localhost:3000> |
| **API reference** | <http://localhost:8000/docs> |
| **Readiness** | <http://localhost:8000/ready> |
| **Service logs** | `.logs/` |

Sign up, open the verification link from the console email in the API log, and create a project key if you want to connect an SDK. Use Ctrl+C to stop launcher-managed processes; the Compose infrastructure remains available.

For existing clones, initialize dependencies with `git submodule update --init --recursive`. If another local stack already owns these ports, stop that stack or configure an isolated environment before starting this one.

Full configuration: [backend example](backend/.env.example) · [deployment modes](docs/deployment-modes.md) · [operator runbooks](docs/runbooks.md)

---

## Verified against the running system

<img src="assets/readme/validation.svg" alt="Dated local validation: 24 browser scenarios, 411 backend tests, 49 Python SDK and CLI plus 31 TypeScript SDK tests, and zero frontend npm audit findings; November 11, 2026 snapshot" width="100%">

This is a **dated local validation snapshot**, not a live CI badge or a claim about every possible deployment.

| Layer | Latest recorded result |
|---|---|
| Real/fixture Playwright scenarios | **24 passed**, 0 failed across the latest applicable full and targeted runs |
| Backend | **411 passed**, 3 documented skips |
| Python SDK + CLI | **49 passed**, no skips or failures |
| TypeScript SDK | **31 passed**, no skips or failures |
| Frontend unit tests | **20 passed** |
| Production build, types, lint, contract suite | **Passed** |
| Frontend dependency audit | **0 vulnerabilities** at validation time |

The real checks include signup and recovery, session refresh, tenant permissions, project changes, invitations, scoped/revoked keys, both SDKs reaching the browser audit trail, confirmed proofs, content checks, alert investigation, downloads, pagination, four concurrent registrations, and mobile evidence pages.

[Read the completion report](docs/completion-report-2026-11-11.md) · [Structured results](docs/audits/2026-11-11/final/results.json) · [Audit details](docs/audits/2026-11-11/final/npm-audit.json)

<details>
<summary><b>Reproduce the checks</b></summary>
<br>

```bash
# Frontend checks
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build

# Backend: real disposable Postgres/Redis via Testcontainers;
# chain integration additionally needs deployed local V2 contracts.
(cd backend && ../.venv/bin/python -m pytest -q)

# Contracts
(cd contracts && forge test)

# Live SDK checks: run against the configured application stack.
.venv/bin/python -m pytest sdk/python/tests sdk/python-cli/tests -ra
npm --prefix sdk/typescript run build
npm --prefix sdk/typescript test

# Browser checks: fixture UI suite, then full real pipeline.
(cd frontend && npx playwright test e2e/product.spec.ts)
(cd frontend && TRUSTCHAIN_FULL_PIPELINE=1 npx playwright test e2e/pipeline.spec.ts)
```

For the expanded real-service browser audit, see [frontend/e2e/README.md](frontend/e2e/README.md). Run model-heavy SDK and pipeline suites sequentially so they do not compete for the same external provider quota. The three backend skips and the targeted browser rerun are described in the completion report.

</details>

---

## Access, privacy, and operating boundaries

| Control | Implementation |
|---|---|
| **Organization and project isolation** | Application authorization plus PostgreSQL row-level security when using the restricted API role |
| **Human sessions** | HttpOnly session cookies, CSRF checks, refresh, and verification/recovery flows |
| **Machine access** | Project-scoped API keys with explicit scopes and revocation |
| **Team permissions** | Viewer, member, admin, and owner roles |
| **Configuration privacy** | SDK registration sends a client-computed configuration hash rather than the raw system prompt |
| **On-chain evidence** | Merkle roots and contract metadata rather than raw input/output content |
| **Signing custody** | Local development signer and optional KMS/Vault backends |
| **Evidence monitoring** | Step, batch-root, identity-event, and anchoring-liveness checks |

The recorded deployment is local: Anvil, console email, and real external model/search services. Production TLS/cookies, external email delivery, public-chain reliability, optional evidence publishing, and other browser engines require their own deployment validation. Model-provider rate limits still apply. A recorded commitment cannot establish that an agent’s real-world statement is correct, or that every action outside instrumentation was captured.

Use the production deployment configuration for public hosting; the local Compose defaults and Anvil development key are for development. [Threat model](docs/threat-model.md) · [Deployment modes](docs/deployment-modes.md) · [Multisig handoff](docs/multisig-admin-handoff.md)

---

## Explore the code

```text
TrustChain/
├── frontend/                 Next.js workspace and Playwright checks
├── backend/
│   ├── agents/               LangGraph agents and model/tool adapters
│   ├── db/                   Durable records, tenancy, and read models
│   ├── anchor_worker/        Outbox claims, Merkle batches, chain submission
│   ├── indexer/              Contract-event ingestion and reconciliation
│   ├── integrity_watchdog/   Continuous verification and liveness checks
│   └── notifications/        Verification, recovery, invitations, alerts
├── contracts/                Solidity contracts, deployments, Foundry tests
├── mcp_servers/              Search and blockchain tool servers
├── sdk/                      Python, TypeScript, and Python CLI packages
├── examples/                 External-agent integration example
├── docs/                     Architecture, decisions, runbooks, validation
└── assets/readme/            Original diagrams and authentic product media
```

[Architecture](docs/architecture.md) · [Design decisions](docs/adr/README.md) · [End-to-end walkthrough](docs/e2e-walkthrough.md) · [API compatibility policy](docs/api-deprecation-policy.md) · [Release process](docs/release-process.md)

<div align="center">

**Build the agent. Keep the evidence. Let someone else verify it.**

[MIT licensed](LICENSE) · [Back to the top](#your-ai-did-the-work-now-show-the-evidence)

</div>
