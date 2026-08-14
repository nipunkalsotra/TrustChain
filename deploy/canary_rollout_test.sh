#!/usr/bin/env bash
set -euo pipefail

# deploy/canary_rollout_test.sh — exercises canary_rollout.sh's real
# control flow (bake loop, failure detection, automatic rollback, state
# file read/write) against a REAL running docker-compose stack and REAL
# curl/docker calls — not a description of what the script would do.
#
# deploy_version() is overridden below to `docker compose start` (fast,
# never rebuilds/recreates anything) instead of the real script's
# `docker compose up -d --build`. That's the one deliberate substitution:
# this repo's local Anvil dev-chain container resets to genesis on any
# `--build` invocation (a well-documented artifact of local dev — see
# canary_rollout.sh's own comment on it), which would make repeated test
# runs fight a moving target unrelated to what's actually being tested
# here (the bake/rollback ORCHESTRATION logic, not image build
# mechanics — a real deploy host has no local Anvil at all, points at
# real Monad RPC, so this quirk is specific to testing against this
# exact local dev stack, not a real deployment concern).
#
# Requires the real docker-compose stack already up and healthy
# (api/anchor-worker/indexer/postgres/redis/anvil with V2 deployed).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export STATE_FILE
STATE_FILE="$(mktemp -d)/.last_known_good_test"
export API_URL="http://localhost:8000"

# shellcheck source=./canary_rollout.sh
source "$REPO_ROOT/deploy/canary_rollout.sh"

deploy_version() {
    local version="$1"
    log "[test stub] deploy_version($version) — docker compose start (no rebuild)"
    (cd "$REPO_ROOT" && docker compose start api anchor-worker indexer) > /dev/null 2>&1
}

assert_eq() {
    local expected="$1" actual="$2" msg="$3"
    if [ "$expected" != "$actual" ]; then
        echo "FAIL: $msg (expected=$expected actual=$actual)"
        exit 1
    fi
    echo "PASS: $msg"
}

echo "=== Pre-flight: real API must be healthy before this test starts ==="
if ! curl -sf "$API_URL/ready" > /dev/null; then
    echo "SKIP: $API_URL/ready is not healthy — bring the stack up first"
    exit 0
fi

echo
echo "=== Test 1: successful bake writes the known-good state file ==="
rm -f "$STATE_FILE"
BAKE_SECONDS=6 BAKE_INTERVAL=2
set +e
( main "1.0.0-test-success" )
exit_code=$?
set -e
assert_eq "0" "$exit_code" "main() exits 0 on a healthy bake"
assert_eq "1.0.0-test-success" "$(cat "$STATE_FILE")" "state file records the new version as known-good"

echo
echo "=== Test 2: mid-bake failure triggers automatic rollback ==="
# The api container is stopped for REAL, 2s into a 10s bake window — a
# genuine health-check failure (connection refused), not a simulated one.
(sleep 2 && (cd "$REPO_ROOT" && docker compose stop api) > /dev/null 2>&1) &
KILLER_PID=$!
BAKE_SECONDS=10 BAKE_INTERVAL=2
set +e
( main "2.0.0-test-bad-deploy" )
exit_code=$?
set -e
wait "$KILLER_PID" 2>/dev/null || true
assert_eq "1" "$exit_code" "main() exits 1 (rolled back) when the bake detects a real failure"
assert_eq "1.0.0-test-success" "$(cat "$STATE_FILE")" "state file still records the ORIGINAL version — rollback doesn't overwrite it with the bad one"

echo
echo "=== Verifying the real API is healthy again after rollback ==="
sleep 2
if curl -sf "$API_URL/ready" > /dev/null; then
    echo "PASS: API is healthy again post-rollback"
else
    echo "FAIL: API did not come back healthy after rollback"
    exit 1
fi

echo
echo "=== Test 3: rollback with no prior known-good version exits 2 ==="
rm -f "$STATE_FILE"
BAKE_SECONDS=4 BAKE_INTERVAL=2
(sleep 1 && (cd "$REPO_ROOT" && docker compose stop api) > /dev/null 2>&1) &
KILLER_PID=$!
set +e
( main "1.0.0-first-deploy-ever-and-its-bad" )
exit_code=$?
set -e
wait "$KILLER_PID" 2>/dev/null || true
assert_eq "2" "$exit_code" "main() exits 2 when rollback has nothing to roll back to"

echo
echo "=== Restoring real stack to a known-good state for anything after this test ==="
(cd "$REPO_ROOT" && docker compose start api anchor-worker indexer) > /dev/null 2>&1
sleep 3
if curl -sf "$API_URL/ready" > /dev/null; then
    echo "Stack confirmed healthy."
else
    echo "WARNING: stack not healthy after test cleanup — check manually."
    exit 1
fi

rm -rf "$(dirname "$STATE_FILE")"
echo
echo "All canary_rollout.sh tests passed."
