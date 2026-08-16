# Release process

## How a release happens

1. A PR merges to `main` and CI (`.github/workflows/test.yml`) passes.
2. `.github/workflows/release.yml` runs [semantic-release](https://semantic-release.gitbook.io/),
   configured via `.releaserc.json`:
   - Reads every commit since the last release tag.
   - Computes the next version from [Conventional Commits](https://www.conventionalcommits.org/)
     (`fix:` → patch, `feat:` → minor, `BREAKING CHANGE:` in a commit body/footer → major).
   - Writes `CHANGELOG.md`, commits it (`chore(release): X.Y.Z [skip ci]`), tags the commit `vX.Y.Z`, and publishes a GitHub Release with generated notes.
   - If no commit since the last release matches a recognized type, **no release happens** — this is normal, not a failure.
3. `.github/workflows/deploy.yml` picks up the new tag, deploys to **staging** automatically, then **production** after manual approval (see below).

## Commit message convention

Every commit on `main` should follow Conventional Commits so the version bump computes correctly:

```
feat: add AWS KMS signer backend
fix: map redis.exceptions.TimeoutError to the builtin TimeoutError in read_events
fix!: require Idempotency-Key format tc_live_/tc_test_ prefix

BREAKING CHANGE: API keys issued before this release must be reissued.
```

Historical commits on this repo predate this convention (see `git log` — a
mix of `feat:`/`fix:`/`chore:` and plain descriptive messages). That's
fine: semantic-release only reads commits *since the last tag*, so it
only matters going forward, not retroactively.

## Configuring the required-approval gate for production

`deploy.yml`'s `production` job targets a GitHub Environment named
`production`. The actual approval gate is a **repo setting**, not
something expressible in the workflow YAML itself:

1. Repo Settings → Environments → New environment → name it `production`.
2. Under "Deployment protection rules", add **Required reviewers** (at
   least one person who must approve before the job runs).
3. Optionally add a wait timer or restrict which branches/tags can
   deploy to it.

Do the same for a `staging` environment if you want any gate there too
(the workflow as written auto-deploys to staging with no gate).

## Docker images

`release.yml`'s `publish-images` job builds and pushes to GHCR
(`ghcr.io/<owner>/trustchain-<component>`), tagged both `:X.Y.Z` and
`:latest`:

| Image | Built from | Serves |
|---|---|---|
| `trustchain-backend` | `backend/Dockerfile` | `api`, `anchor-worker`, and `indexer` — same image, docker-compose.yml only changes the `command:` per service |
| `trustchain-mcp-search` | `mcp_servers/web_search/Dockerfile` | the web-search MCP server |
| `trustchain-mcp-blockchain` | `mcp_servers/blockchain/Dockerfile` | the blockchain MCP server |

The frontend has no image here — it has no `Dockerfile` and isn't part
of `docker-compose.yml`; it's deployed separately (Vercel, per
`main.py`'s CORS-allowlist comments referencing a deployed frontend URL),
with its own git-integration deploy flow.

Every pushed image also gets an explicit SLSA provenance attestation
(`provenance: true`) and is signed by digest with cosign, keyless — the
same GitHub OIDC / Fulcio / Rekor mechanism `test.yml`'s `sdk-integration`
job already uses for its SBOM, applied here to the actual images this
job publishes. Verify a given release's image:

```bash
cosign verify --certificate-identity-regexp \
  'https://github.com/<owner>/TrustChain/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<owner>/trustchain-backend@<digest>
```

## OpenAPI schema snapshot

`release.yml`'s `publish-openapi-snapshot` job attaches an `openapi.json`
asset (`backend/scripts/generate_openapi_schema.py`'s output at that
tag) to every GitHub Release. This is what
[`docs/api-deprecation-policy.md`](api-deprecation-policy.md)'s
automated `api-compat-check` CI job diffs the next release's API
surface against to catch accidental breaking changes — see that doc for
the mechanism and its current bootstrap-state caveats (no release has
been cut yet, so this hasn't produced a real snapshot as of this
writing).

## Canary rollout + automatic rollback

`deploy/canary_rollout.sh` is the actual deploy mechanism `deploy.yml`'s
placeholder SSH steps invoke: deploy the new version, hold it through a
**bake period** of repeated real `/ready` checks (not one curl
immediately after startup — a deploy that crashes 20s in or wedges under
its first few requests sails straight past a single check), and
**automatically roll back** to the last known-good version the instant
any check in that window fails, rather than leaving a broken version
live until someone notices.

This repo's actual deployment topology is a single docker-compose host
(see the limitation below), not a fleet behind a load balancer — so a
textbook weighted-traffic canary (5% of live traffic, then 25%, then
100%) isn't something this project can run. The bake-then-commit-or-
rollback pattern above is the right shape for *this* topology; the script
works identically against a real host once one exists (`DEPLOY_MODE=pull`
pulls the released image tag from GHCR instead of building from source).

Verified against this repo's own docker-compose stack, not just written
and assumed correct — `deploy/canary_rollout_test.sh` exercises the real
control flow with real `docker`/`curl` calls: a genuine successful bake
that records the new version as known-good, a real mid-deploy container
crash that the bake loop actually detects and automatically rolls back
from (restoring the previous version and confirming *it* bakes clean
too), and the no-prior-known-good-version edge case. Run it yourself
against a local `docker compose up` stack: `bash deploy/canary_rollout_test.sh`.

## Honest limitation: no real deployment target yet

This repo has no real staging or production server/cluster configured (no
cloud account, no kubeconfig, no SSH target) — `deploy.yml`'s `staging`
and `production` jobs are conditional on that, not silent placeholders:

- **If `DEPLOY_HOST`/`DEPLOY_SSH_KEY` GitHub Environment secrets exist**
  on that job's Environment, it SSHes there for real and runs
  `DEPLOY_MODE=pull bash deploy/canary_rollout.sh <version>` on that
  host, exactly as documented below.
- **Until then**, the same job runs `deploy/canary_rollout.sh` FOR REAL
  anyway — `DEPLOY_MODE=build`, against that job's own ephemeral
  docker-compose stack (bootstrapped the same way `test.yml`'s
  `sdk-integration` job is: postgres/redis/anvil/mcp servers, migrations,
  V2 contracts deployed to Anvil). This is `canary_rollout.sh` itself,
  unmodified — not `canary_rollout_test.sh`'s stubbed `deploy_version()`
  — genuinely deploying, baking against real `/ready` checks, and rolling
  back automatically on a failed bake, exactly like it would against a
  real host. Verified locally end-to-end (`bash deploy/canary_rollout.sh
  ci-test-1.0.0` against this repo's own running stack: real startup
  wait, six real health checks over a real 30s bake window, a real
  `deploy/.last_known_good` write on success).

What's ALSO real (and unaffected by whether a host exists): the release
computation (verified via a real `semantic-release --dry-run` against
this repo's actual commit history and GitHub remote — it correctly
resolved the repo URL and reached the push-permission check, only failing
on authentication because no real write-scoped token was used for that
verification run), the image build/push wiring, the environment-gate
structure, and the canary/rollback script's own control flow (see above —
`canary_rollout_test.sh`'s synthetic bake/crash/rollback scenarios, run
separately from the real end-to-end run described above).

Once a real target exists, only two things change — the job code itself
doesn't need touching, it already branches on these:

1. Add `DEPLOY_SSH_KEY` / `DEPLOY_HOST` (and the production equivalents —
   GitHub Environment secrets are scoped per-environment, so `staging`'s
   and `production`'s own values of the same-named secret are already
   kept separate) as environment secrets on the `staging`/`production`
   GitHub Environments (not repo-wide secrets — environment secrets are
   only readable by jobs targeting that environment). The job's `if`
   branch picks up the real SSH path automatically the next run.
2. Replace the placeholder `environment.url` values with the real
   staging/production URLs (they show up as clickable links on the
   deployment in GitHub's UI once real).

## Manually triggering a deploy

`deploy.yml` also accepts `workflow_dispatch` with a `version` input, for
redeploying a specific already-released version (e.g. rolling forward
after a failed staging smoke test, or rolling back to a known-good tag)
without needing a new release.
