# 0011 — Canary rollout for a single-host deploy

**Status:** Accepted

## Context

`deploy.yml`'s deploy steps were, and still are, honest placeholders —
this project has no real staging/production cloud target (no account,
no server, no kubeconfig; see `docs/release-process.md`'s "Honest
limitation" section). But even as placeholders, they described a
single-shot deploy with no structure for catching a bad release before
it's fully live, and no automated recovery path — exactly the gap
flagged as missing ("no canary/rollback in deploy.yml").

A textbook weighted-traffic canary (5% of live traffic, then 25%, then
100%, via a load balancer or service mesh) isn't something this
project's actual deployment topology can run — it's a single
docker-compose host, not a fleet.

## Decision

`deploy/canary_rollout.sh`: deploy the new version, hold it through a
**bake period** of repeated real `/ready` checks (not one curl
immediately after startup, which a deploy that crashes 20s in or wedges
under its first few requests would sail straight past), and
**automatically roll back** to the last known-good version — recorded
in a host-local state file, `deploy/.last_known_good` — the instant any
check in that window fails. This is the honest, right-shaped
interpretation of "canary" for a single-host deploy: a soak/bake window
with automated rollback, not a traffic split that would require
infrastructure this project doesn't have.

The script works two ways with the same code path: `DEPLOY_MODE=build`
(default — builds from source, what's actually testable today against
this repo's own docker-compose stack) or `DEPLOY_MODE=pull` (pulls a
released tag from GHCR, the mode a real deploy host would use).

Verified against the real stack, not just written: `deploy/
canary_rollout_test.sh` sources the script and exercises `bake()`/
`rollback()`/`main()`'s actual control flow with real `docker`/`curl`
calls — a genuine successful bake, a real mid-deploy container crash
that the bake loop detects and automatically rolls back from (and
confirms the rollback *itself* bakes clean), and the no-prior-known-good-
version edge case.

## Alternatives considered

- **Leave the placeholder as a flat, unstructured single-shot deploy**
  and treat "canary/rollback" as something to design only once real
  infrastructure exists. Rejected: the *shape* of deploy/rollback logic
  doesn't depend on having a real host — it's testable right now
  against the project's own docker-compose stack, and building it now
  means it's ready to point at a real host later rather than designed
  from scratch under deploy pressure.
- **A true weighted-traffic canary design**, documented but unbuildable
  without real infrastructure. Rejected as the wrong shape even in
  principle for this project's actual (single-host) deployment
  topology — describing infrastructure that doesn't exist and wouldn't
  even be the right target once it does isn't more honest than
  matching the real topology.
- **Fail immediately on the first bad health check** (no bake window,
  matching the original placeholder's implicit "smoke test once, then
  done" shape). Rejected: a bake window catches failure modes a single
  post-deploy check can't — a service that responds fine for the first
  few seconds and then wedges or crashes shortly after.

## Consequences

- A real, non-hypothetical bug was found and fixed building this: the
  test harness's `deploy_version` stub (`docker compose start`, not a
  full rebuild) returns before uvicorn is actually accepting requests —
  `bake()`'s very first check would fail on a perfectly good deploy
  purely from checking too early, indistinguishable from a genuinely
  broken one. Fixed with a `wait_for_startup()` grace period *before*
  the bake window starts counting (up to `STARTUP_TIMEOUT`, default
  30s) — the same class of timing assumption a real deploy host's
  container startup would also need accounted for, not specific to the
  test stub.
- `deploy/.last_known_good` is host-local, gitignored state — it does
  not survive a fresh checkout or a different deploy host, by design
  (it tracks *this host's* currently-running version, not a
  repo-wide fact). A multi-host deployment would need each host's own
  state file, or a shared state store this single-host design doesn't
  need yet.
- Rollback failing its own bake (`exit 2`) is treated as a genuine
  incident requiring manual intervention, not something the script
  retries further — deliberately: an automated system that keeps
  trying things after two consecutive failures risks making a bad
  situation worse rather than surfacing it clearly to a human.
