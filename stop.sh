#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  TrustChain — stop everything start.sh can leave running
#  Run from project root: ./stop.sh
#
#  start.sh's own Ctrl+C trap only kills the PIDs it tracked in the SAME
#  shell session, and deliberately never stops postgres/redis/anvil (so a
#  normal Ctrl+C/restart cycle keeps DB data and chain state warm). That
#  means two things can outlive a session:
#    1. The plain python/node processes (api, MCP servers, anchor-worker,
#       indexer, integrity-watchdog, frontend) if the terminal was closed,
#       the machine slept, or the process was killed some other way than
#       Ctrl+C in that same shell — orphaned, still holding their ports.
#    2. The docker compose stack (postgres/redis/anvil), which start.sh
#       never stops on exit at all, by design.
#  This script frees all of it, independent of how the previous session
#  ended — matched by PORT, not by a PID file (start.sh doesn't write one),
#  so it works even across a closed terminal or a reboot-surviving orphan.
# ═══════════════════════════════════════════════════════════════════

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}Stopping TrustChain...${NC}"
echo ""

# ── 1. Plain background processes, matched by the port each one binds ──
# (mirrors start.sh's [1/7]..[7/7]: mcp_search, mcp_blockchain, api,
# anchor-worker's metrics port, indexer's metrics port, watchdog's metrics
# port, frontend)
declare -A PORT_LABELS=(
    [8000]="FastAPI backend"
    [8001]="MCP web_search server"
    [8002]="MCP blockchain server"
    [3000]="Next.js frontend"
    [9101]="Anchor worker"
    [9102]="Indexer"
    [9103]="Integrity watchdog"
)

for port in "${!PORT_LABELS[@]}"; do
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            kill "$pid" 2>/dev/null
        done
        echo -e "  ${GREEN}✓${NC} ${PORT_LABELS[$port]} ${DIM}(port $port, PID(s) $pids)${NC}"
    else
        echo -e "  ${DIM}· ${PORT_LABELS[$port]} (port $port) — not running${NC}"
    fi
done

# Give SIGTERM a moment before checking for stragglers.
sleep 1
for port in "${!PORT_LABELS[@]}"; do
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null
        done
        echo -e "  ${YELLOW}⚠${NC} ${PORT_LABELS[$port]} didn't stop on SIGTERM — sent SIGKILL"
    fi
done

echo ""

# ── 2. Docker compose stack (postgres/redis/anvil + friends) ──────────
# Checked across EVERY docker context, not just the currently active one —
# `docker compose up` under one context and `docker compose down`/`stop`
# under another silently ignores containers that exist under the other
# daemon, which is exactly what caused start.sh's port-5432 collision (the
# stack was healthy under the `default` context while the CLI's active
# context had switched to `desktop-linux`). `docker compose stop` (not
# `down`) — ports are freed the same way, but named volumes AND the
# containers themselves survive, so the next start.sh run doesn't need to
# re-migrate/re-deploy from scratch.
cd "$ROOT"
CURRENT_CTX="$(docker context show 2>/dev/null || echo "default")"
FOUND_ANY=0

while IFS= read -r ctx; do
    [ -z "$ctx" ] && continue
    ids="$(docker --context "$ctx" compose ps -q 2>/dev/null || true)"
    if [ -n "$ids" ]; then
        FOUND_ANY=1
        echo -e "  ${CYAN}docker context '${ctx}'${NC} — stack found, stopping..."
        docker --context "$ctx" compose stop 2>&1 | sed 's/^/    /'
        if [ "$ctx" != "$CURRENT_CTX" ]; then
            echo -e "  ${YELLOW}⚠ that stack was running under context '${ctx}', not your active context '${CURRENT_CTX}'.${NC}"
            echo -e "  ${DIM}  If this happens again, run: docker context use ${ctx}${NC}"
        fi
    fi
done < <(docker context ls --format '{{.Name}}' 2>/dev/null)

if [ "$FOUND_ANY" -eq 0 ]; then
    echo -e "  ${DIM}· No docker compose stack found running under any context.${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}All stopped. Ports freed.${NC}"
