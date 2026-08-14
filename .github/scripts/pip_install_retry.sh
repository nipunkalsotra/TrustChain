#!/usr/bin/env bash
set -uo pipefail

# .github/scripts/pip_install_retry.sh — `pip install "$@"` with retries.
#
# CI hit real `ConnectionResetError(104, 'Connection reset by peer')`
# failures from pip's vendored cachecontrol/requests during `pip install`
# (both the backend job's requirements-dev.txt install and
# sdk-integration's schemathesis install) — a transient PyPI/network
# blip, not a real dependency problem (the same install succeeds locally
# and on retry). pip's own built-in retry logic doesn't reliably cover a
# raw connection reset that happens before a download starts, so this
# wraps the whole command in a shell-level retry instead of trusting
# pip's internal handling to catch every case.

MAX_ATTEMPTS=5
DELAY_SECONDS=10

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    if pip install "$@"; then
        exit 0
    fi
    echo "pip install failed (attempt ${attempt}/${MAX_ATTEMPTS})" >&2
    if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
        echo "retrying in ${DELAY_SECONDS}s..." >&2
        sleep "$DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
done

echo "pip install failed after ${MAX_ATTEMPTS} attempts" >&2
exit 1
