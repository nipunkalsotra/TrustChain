#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  TrustChain — single startup script
#  Run from project root: ./start.sh
#  Stops everything cleanly on Ctrl+C
# ═══════════════════════════════════════════════════════════════════

set -e

# ── Colours ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# ── Paths ──────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin/activate"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
MCP_SEARCH="$ROOT/mcp_servers/web_search/server.py"
MCP_CHAIN="$ROOT/mcp_servers/blockchain/server.py"

# ── PID tracking (so we can kill everything on exit) ──────────────
PIDS=()

# ── Banner ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}"
echo "  ████████╗██████╗ ██╗   ██╗███████╗████████╗"
echo "     ██╔══╝██╔══██╗██║   ██║██╔════╝╚══██╔══╝"
echo "     ██║   ██████╔╝██║   ██║███████╗   ██║   "
echo "     ██║   ██╔══██╗██║   ██║╚════██║   ██║   "
echo "     ██║   ██║  ██║╚██████╔╝███████║   ██║   "
echo "     ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ╚═╝  "
echo -e "          ${DIM}CHAIN${NC}${CYAN}${BOLD}                               "
echo -e "${NC}"
echo -e "${DIM}  Immutable Agent Audit · Monad Blockchain${NC}"
echo ""

# ── Cleanup on exit ────────────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down TrustChain...${NC}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null && echo -e "${DIM}  killed PID $pid${NC}"
    done
    echo -e "${GREEN}All services stopped. Goodbye.${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Check venv exists ──────────────────────────────────────────────
if [ ! -f "$VENV" ]; then
    echo -e "${RED}✗ .venv not found at $ROOT/.venv${NC}"
    echo -e "${DIM}  Run: python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt${NC}"
    exit 1
fi

# ── Check frontend node_modules ────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
    echo -e "${YELLOW}⚠ node_modules not found — running npm install...${NC}"
    cd "$FRONTEND" && npm install --silent
    cd "$ROOT"
fi

# ── Log directory ──────────────────────────────────────────────────
mkdir -p "$ROOT/.logs"
SEARCH_LOG="$ROOT/.logs/mcp_search.log"
CHAIN_LOG="$ROOT/.logs/mcp_blockchain.log"
API_LOG="$ROOT/.logs/fastapi.log"
FRONTEND_LOG="$ROOT/.logs/frontend.log"

echo -e "${BOLD}Starting services...${NC}"
echo ""

# ── 1. MCP web_search server ───────────────────────────────────────
echo -e "  ${CYAN}[1/4]${NC} MCP web_search server    ${DIM}→ localhost:8001${NC}"
source "$VENV"
cd "$ROOT"
python "$MCP_SEARCH" > "$SEARCH_LOG" 2>&1 &
PIDS+=($!)
sleep 1

if kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
else
    echo -e "       ${RED}✗ failed to start — check .logs/mcp_search.log${NC}"
fi

# ── 2. MCP blockchain server ───────────────────────────────────────
echo -e "  ${CYAN}[2/4]${NC} MCP blockchain server     ${DIM}→ localhost:8002${NC}"
python "$MCP_CHAIN" > "$CHAIN_LOG" 2>&1 &
PIDS+=($!)
sleep 1

if kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
else
    echo -e "       ${RED}✗ failed to start — check .logs/mcp_blockchain.log${NC}"
fi

# ── 3. FastAPI backend ─────────────────────────────────────────────
echo -e "  ${CYAN}[3/4]${NC} FastAPI backend           ${DIM}→ localhost:8000${NC}"
cd "$BACKEND"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload > "$API_LOG" 2>&1 &
PIDS+=($!)
cd "$ROOT"

# Wait for FastAPI to be ready
echo -ne "       ${DIM}waiting for backend"
for i in {1..20}; do
    sleep 0.5
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${NC}"
        echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
        break
    fi
    echo -ne "."
    if [ $i -eq 20 ]; then
        echo -e "${NC}"
        echo -e "       ${YELLOW}⚠ backend slow to start — check .logs/fastapi.log${NC}"
    fi
done

# ── 4. Next.js frontend ────────────────────────────────────────────
echo -e "  ${CYAN}[4/4]${NC} Next.js frontend          ${DIM}→ localhost:3000${NC}"
cd "$FRONTEND"
npm run dev > "$FRONTEND_LOG" 2>&1 &
PIDS+=($!)
cd "$ROOT"

# Wait for Next.js to be ready
echo -ne "       ${DIM}waiting for frontend"
for i in {1..30}; do
    sleep 0.5
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo -e "${NC}"
        echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
        break
    fi
    echo -ne "."
    if [ $i -eq 30 ]; then
        echo -e "${NC}"
        echo -e "       ${YELLOW}⚠ frontend slow to start — check .logs/frontend.log${NC}"
    fi
done

# ── Ready ──────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}${BOLD}  ✓  TrustChain is running${NC}"
echo -e "  ${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Dashboard   ${NC}${CYAN}http://localhost:3000${NC}"
echo -e "  ${BOLD}API docs    ${NC}${CYAN}http://localhost:8000/docs${NC}"
echo -e "  ${BOLD}Health      ${NC}${CYAN}http://localhost:8000/health${NC}"
echo ""
echo -e "  ${DIM}Logs:${NC}"
echo -e "  ${DIM}  MCP search    .logs/mcp_search.log${NC}"
echo -e "  ${DIM}  MCP chain     .logs/mcp_blockchain.log${NC}"
echo -e "  ${DIM}  FastAPI       .logs/fastapi.log${NC}"
echo -e "  ${DIM}  Frontend      .logs/frontend.log${NC}"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services${NC}"
echo ""

# ── Tail FastAPI log so you see agent output live ──────────────────
tail -f "$API_LOG" &
PIDS+=($!)

# ── Keep alive ─────────────────────────────────────────────────────
wait