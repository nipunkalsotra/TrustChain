# SLOs and error budget policy

What this system commits to being reliable *about*, how that's measured
today, and what happens when it isn't. Written against what this
deployment actually is — see the "Honest starting point" section below
before reading any number here as a promise it isn't.

## Honest starting point

`docs/architecture.md`'s "Deployment topology" section and
[ADR-0011](adr/0011-canary-rollout-for-a-single-host-deploy.md) are the
real constraints this policy has to be honest about:

- **One host, no fleet, no load balancer.** There's no persistent
  staging/production cloud target configured yet (`docs/release-
  process.md`'s "Honest limitation" section) — what exists is a single
  docker-compose host, reached by a canary-rollout-then-bake-or-rollback
  script over SSH. A bad deploy or a host failure is a total outage for
  the duration it takes to roll back or restart, not a partial one a
  load balancer routes around.
- **No multi-nines commitment.** Promising 99.9%/99.99% uptime on a
  single host with no failover would be a number nobody could actually
  defend at 2am. The targets below are internal operational goals for
  this deployment stage, not a customer-facing SLA.
- **The thresholds below are starting points, not validated
  numbers** — `docker/prometheus/alerts.yml`'s own header says this
  explicitly ("not load-tested production numbers... tune against real
  traffic before trusting these as paging thresholds"), and it's just
  as true here, since this policy's burn-rate math is built directly on
  top of those same thresholds. Revisit both together once real traffic
  data exists.

Given that, this doc's job is narrower than a classic SLA: define what's
actually measured, what "budget" means for a single-host deployment,
and make the alert thresholds that already exist in this repo do double
duty as error-budget policy instead of leaving them as disconnected
paging rules nobody's reasoned about as a set.

## Service Level Indicators (SLIs)

Three SLIs, each backed by a real Prometheus metric already emitted by
`backend/observability.py` — no new instrumentation needed to compute
any of these.

| SLI | What it measures | Metric(s) |
|---|---|---|
| **API availability** | Fraction of HTTP responses that aren't a 5xx | `http_requests_total{status=~"5.."}` / `http_requests_total` |
| **API latency** | Read-path response time | `http_request_duration_seconds` |
| **Pipeline success rate** | Fraction of started agent pipeline runs that complete (vs. fail) | `pipeline_runs_total{status="completed"}` / `pipeline_runs_total{status="started"}` |
| **Anchoring freshness** | How current the on-chain audit trail is vs. reality | `anchor_outbox_pending`, `indexer_poll_lag_blocks` |

Anchoring freshness is included as a first-class SLI, not just an
infrastructure metric, because it's this product's actual core promise
(a durable, verifiable on-chain audit trail) — API uptime alone doesn't
capture "did the thing that happened actually get anchored," which is
the whole point.

**Explicitly not an SLI yet**: anything per-tenant. `observability.py`'s
own docstring is deliberate about this — no metric here carries a
project/org/user label (Prometheus cardinality), so a per-tenant SLI
would have to be computed from structured logs (`request_id`/`run_id`/
`project_id`/`org_id` correlation, `backend/logging_config.py`), not
from these Prometheus series directly.

## Service Level Objectives (SLOs)

| SLI | Objective | Window | Basis |
|---|---|---|---|
| API availability | ≥ 95% of requests are not 5xx | rolling 5m (matches the alert window) | `HighHTTP5xxRate`'s own >5% threshold, restated as the objective it implies |
| API read-path latency | p95 < 1000ms | — | `k6/load-test.js`'s load-test threshold for `trust-scores`/`leaderboard`/`audit-log`/`runs` — a *test* pass/fail bar, not yet a live-traffic-measured number, cited here as the closest validated latency figure that exists |
| `POST /run-agent` latency | p95 < 2000ms (the enqueue call itself, not the pipeline run it kicks off) | — | same k6 load-test file |
| Pipeline success rate | ≥ 80% of started runs complete | rolling 15m | `PipelineFailureRateHigh`'s own >20% failure threshold, restated as the objective it implies |
| Anchoring backlog | < 500 pending steps | sustained 10m | `AnchorOutboxBacklogGrowing`'s threshold |
| Anchoring data loss | zero dead-lettered steps, ever | — | `AnchorOutboxStepsDeadLettered` fires on `for: 0m` (immediately) precisely because this objective has no acceptable non-zero rate — see that alert's own annotation in `alerts.yml` |

Every number above is deliberately the *same* number an existing alert
already pages on (see the next section) — this policy doesn't invent a
stricter internal target that then needs separately reconciled against
what actually pages. If the two ever need to diverge (e.g. an SLO
tighter than the paging threshold, to catch a burn *before* it's bad
enough to page), that's a real design decision to make explicitly, not
a drift to let happen silently.

## Error budget

For each SLO above, the error budget is simply *the gap the objective
already leaves* — e.g. API availability's 95% objective means a 5%
error budget over the same 5-minute window `HighHTTP5xxRate` uses.
There's no separate longer-window (e.g. 30-day) budget tracked yet,
because there's no persistent production deployment accumulating that
history yet (see "Honest starting point" above) — this is a **live
burn-rate policy**, not a **calendar-window budget policy**, until a
real deployment exists to compute the latter against.

## Burn-rate response policy

This maps directly onto `docker/prometheus/alerts.yml`'s existing
`severity` labels — the policy IS the alert severities already assigned,
made explicit as a response ladder rather than left implicit per-alert:

- **`critical`** (`HighHTTP5xxRate`, `AnchorBatchesFailingRepeatedly`,
  `AnchorWalletBalanceLow`, `AnchorOutboxStepsDeadLettered`) — budget is
  burning fast enough, or has already been irreversibly spent (dead-
  lettering), that it pages immediately. Follow the matching runbook
  entry in `docs/runbooks.md` — every critical alert here has one.
- **`warning`** (`PipelineFailureRateHigh`, `AnchorOutboxBacklogGrowing`,
  `IndexerFallingBehind`, `RpcCircuitBreakerOpen`, `RpcCallFailuresElevated`,
  `TokenBudgetCeilingBreached`, `AnchorGasCeilingBreached`,
  `AnchorReaperResetsCrashLoop`, `AgentIntegrityViolationsDetected`) —
  budget is burning at a rate worth investigating within the alert's own
  `for:` window, not necessarily an immediate page. Same runbook
  cross-reference pattern. Three of these
  (`TokenBudgetCeilingBreached`/`AnchorGasCeilingBreached`) are a
  distinct flavor within `warning`: not a burn-rate signal at all, but
  an org hitting its own configured ceiling — expected, correct
  behavior surfaced for operator action, not degradation. See each
  alert's own comment in `alerts.yml`.
- **`info`** (`RateLimitRejectionsSpike`) — not a budget-burn signal on
  its own (could be legitimate traffic, could be abuse) — visibility,
  not a response-ladder rung.

**What actually happens when budget burns, given the single-host
deploy model**: unlike a fleet where a bad release degrades gradually
as it rolls out, a bad deploy here is closer to instantaneous full-
outage-or-not — [ADR-0011](adr/0011-canary-rollout-for-a-single-host-deploy.md)'s
bake-then-commit-or-automatic-rollback is the actual mitigation, not a
gradual traffic-shifting canary. A budget-burning deploy should trip
the canary script's own health check (`/ready`) and roll back
automatically before it ever reaches the alert thresholds above; these
alerts are the backstop for problems that show up *after* the bake
window looked fine, not the primary defense against a bad release.

## Known gaps

Not a complete picture yet — worth tracking explicitly rather than
silently treating this as done:

- **Five real metrics exist with no alert yet**: `anchor_batch_gas_cost_wei`,
  `anchor_gas_price_wei`, `rpc_call_failures_total`, `llm_tokens_used_total`,
  `agent_integrity_violations_total` (all added alongside this policy —
  see `backend/observability.py`). None has a burn-rate objective here
  yet because none has an alert threshold yet to base one on — adding
  alerts for these is separate, tracked work; once they exist, fold
  their thresholds into this doc the same way the existing ones were.
- **No real multi-day/30-day error budget** — see "Error budget" above;
  this needs an actual persistent deployment accumulating history
  before that number means anything.
- **k6 thresholds are test thresholds, not live SLOs** — cited above as
  the best available latency numbers, but they're pass/fail bars for a
  synthetic load test (`k6/load-test.js`, `k6/soak-test.js`), not
  something measured against real production traffic. Revisit once
  real traffic exists to measure against instead.
