#!/usr/bin/env bash
set -euo pipefail

# deploy/canary_rollout.sh — deploy, bake, and automatically roll back on
# failure, for TrustChain's actual deployment topology: a single
# docker-compose host (see docs/release-process.md's "Honest limitation"
# section — there is no real staging/production server yet, no load
# balancer, no fleet to split live traffic across, so a textbook
# weighted-traffic canary isn't something this project can run). What IS
# real and IS the right shape for a single-host deploy: bring up the new
# version, hold it through a BAKE PERIOD of repeated real health checks —
# not just one curl immediately after startup, which a deploy that
# crashes 20s in or wedges under its first few requests would sail
# straight past — and roll back automatically and immediately if any
# check in that window fails, rather than leaving a broken version live
# until a human notices.
#
# Two ways to run this, same script either way:
#   - Locally against docker-compose (DEPLOY_MODE=build, the default):
#     builds from source, same as any other local dev use of this
#     compose file. This is what's actually tested (see
#     deploy/canary_rollout_test.sh) — no fabricated "it works" claim.
#   - Against a real deploy host once one exists (DEPLOY_MODE=pull):
#     pulls the named tag from GHCR (docs/release-process.md's image
#     table) instead of building. deploy.yml's placeholder SSH step is
#     just `ssh $HOST 'DEPLOY_MODE=pull deploy/canary_rollout.sh $VERSION'`.
#
# State: the last known-good version is recorded in
# deploy/.last_known_good (gitignored — host-local state, not something
# to commit), created on first successful bake, read on rollback.
#
# Usage: deploy/canary_rollout.sh <version> [bake_seconds] [bake_interval_seconds]
# Exit codes: 0 = deployed and healthy. 1 = bad deploy, rolled back to
# the previous known-good version successfully. 2 = bad deploy AND the
# rollback itself failed its own bake — a genuine incident, not
# something this script can resolve on its own.

# Defaults for anything not already set by a caller — deploy/
# canary_rollout_test.sh sources this file (not runs it) to exercise
# bake()/rollback()/main()'s logic directly against real curl/docker
# calls without going through argument parsing, so those must already be
# safe to reference before VERSION/BAKE_SECONDS are known.
BAKE_SECONDS="${BAKE_SECONDS:-30}"
BAKE_INTERVAL="${BAKE_INTERVAL:-5}"
DEPLOY_MODE="${DEPLOY_MODE:-build}"   # build (local/dev) | pull (real GHCR-backed host)
API_URL="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${STATE_FILE:-$SCRIPT_DIR/.last_known_good}"
# Same set deploy.yml's placeholder always deployed together (same image
# versions, one release). /ready only checks the core database+chain
# path (api/anchor-worker/indexer) — mcp-search/mcp-blockchain ride along
# on the same deploy without their own bake signal, since they're
# supporting tool servers the pipeline calls out to, not on the
# audit-log write path this whole security pass has focused on.
SERVICES=(api anchor-worker indexer mcp-search mcp-blockchain)

log() { echo "[canary] $*"; }

deploy_version() {
    local version="$1"
    log "deploying version=$version (mode=$DEPLOY_MODE)"
    export TRUSTCHAIN_VERSION="$version"
    if [ "$DEPLOY_MODE" = "pull" ]; then
        docker compose pull "${SERVICES[@]}"
        docker compose up -d "${SERVICES[@]}"
    else
        docker compose up -d --build "${SERVICES[@]}"
    fi
}

health_check_once() {
    curl -sf "${API_URL}/ready" > /dev/null 2>&1
}

# A container that was just started (docker compose up -d / start) isn't
# necessarily accepting requests the instant that command returns —
# uvicorn/Python startup, DB connection warmup, etc. take a real few
# seconds. Without this, bake()'s FIRST check would routinely fail on a
# perfectly good deploy for no reason other than checking too early,
# indistinguishable from a genuinely broken one (found by
# canary_rollout_test.sh's rollback test — the rolled-back container
# hadn't finished booting by the time the immediate post-deploy check
# ran). STARTUP_TIMEOUT is a one-time grace period BEFORE the bake
# window starts counting, not part of the bake window itself.
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-30}"
STARTUP_POLL_INTERVAL="${STARTUP_POLL_INTERVAL:-2}"

wait_for_startup() {
    local elapsed=0
    log "waiting up to ${STARTUP_TIMEOUT}s for the deploy to start accepting requests"
    while [ "$elapsed" -lt "$STARTUP_TIMEOUT" ]; do
        if health_check_once; then
            log "accepting requests after ${elapsed}s"
            return 0
        fi
        sleep "$STARTUP_POLL_INTERVAL"
        elapsed=$((elapsed + STARTUP_POLL_INTERVAL))
    done
    log "never started accepting requests within ${STARTUP_TIMEOUT}s"
    return 1
}

bake() {
    local elapsed=0
    if ! wait_for_startup; then
        return 1
    fi
    log "baking for ${BAKE_SECONDS}s (checking ${API_URL}/ready every ${BAKE_INTERVAL}s)"
    while [ "$elapsed" -lt "$BAKE_SECONDS" ]; do
        if ! health_check_once; then
            log "health check FAILED at ${elapsed}s into bake"
            return 1
        fi
        log "health check OK at ${elapsed}s"
        sleep "$BAKE_INTERVAL"
        elapsed=$((elapsed + BAKE_INTERVAL))
    done
    return 0
}

rollback() {
    if [ ! -f "$STATE_FILE" ]; then
        log "ROLLBACK REQUESTED but no prior known-good version on file — nothing to roll back to. Manual intervention required."
        exit 2
    fi
    local previous
    previous="$(cat "$STATE_FILE")"
    log "rolling back to previous known-good version: $previous"
    deploy_version "$previous"
    if bake; then
        log "rollback to $previous succeeded and is healthy"
    else
        log "ROLLBACK ITSELF FAILED HEALTH CHECKS — this is now a genuine incident, manual intervention required"
        exit 2
    fi
}

main() {
    local version="$1"
    if [ -f "$STATE_FILE" ]; then
        log "previous known-good version on file: $(cat "$STATE_FILE")"
    else
        log "no prior known-good version recorded — first deploy, nothing to roll back to on failure"
    fi

    deploy_version "$version"
    if bake; then
        echo "$version" > "$STATE_FILE"
        log "version=$version baked successfully and is now the known-good version"
    else
        log "bake failed for version=$version — rolling back automatically"
        rollback
        exit 1
    fi
}

# Only parse args and run when EXECUTED directly (./canary_rollout.sh ...
# or bash canary_rollout.sh ...) — deploy/canary_rollout_test.sh sources
# this same file to call bake()/rollback()/main() directly against a
# real running stack without re-triggering a full image rebuild for
# every assertion (see that file for why: rebuilding this repo's local
# Anvil dev-chain container resets it to genesis, an artifact of local
# dev that a real deployment target — pointed at real Monad RPC, no
# Anvil in the stack at all — would never hit).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    VERSION="${1:?usage: canary_rollout.sh <version> [bake_seconds] [bake_interval_seconds]}"
    BAKE_SECONDS="${2:-$BAKE_SECONDS}"
    BAKE_INTERVAL="${3:-$BAKE_INTERVAL}"
    main "$VERSION"
fi
