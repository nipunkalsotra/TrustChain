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
    echo -e "${DIM}  Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements-dev.txt${NC}"
    exit 1
fi

# ── Check frontend node_modules ────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
    echo -e "${YELLOW}⚠ node_modules not found — running npm install...${NC}"
    cd "$FRONTEND" && npm install --silent
    cd "$ROOT"
fi

# ── Load backend/.env into this shell's environment ─────────────────
# docker-compose.yml gives every backend-dependent service (api,
# mcp-blockchain, anchor-worker, indexer) `env_file: ./backend/.env` —
# same idea here, so every python process this script launches below
# inherits GROQ_API_KEY/TAVILY_API_KEY/MONAD_RPC_URL/PRIVATE_KEY/JWT_SECRET
# as real env vars, regardless of its own cwd. This matters concretely for
# mcp_servers/blockchain/server.py: its own load_dotenv() call runs AFTER
# it already imports blockchain.client (which reads get_settings() at
# import time, module-level) — too late — and even if the ordering were
# fixed, python-dotenv's cwd-relative search wouldn't find backend/.env
# from this script's repo-root cwd anyway (found by actually launching it
# from here and watching it crash with a pydantic "jwt_secret: Field
# required" error, not by inspection). Real env vars exported before the
# process even starts sidestep both problems.
if [ ! -f "$BACKEND/.env" ]; then
    echo -e "${RED}✗ backend/.env not found${NC}"
    echo -e "${DIM}  Run: cp backend/.env.example backend/.env and fill it in${NC}"
    exit 1
fi
set -a
source "$BACKEND/.env"
set +a

# ── Log directory (created early — migrations/deploy steps below log here too) ──
mkdir -p "$ROOT/.logs"
SEARCH_LOG="$ROOT/.logs/mcp_search.log"
CHAIN_LOG="$ROOT/.logs/mcp_blockchain.log"
API_LOG="$ROOT/.logs/fastapi.log"
FRONTEND_LOG="$ROOT/.logs/frontend.log"

# ── Ensure Postgres is up (backend/db is Postgres-backed as of Phase 2) ──
if ! docker compose ps postgres --status running >/dev/null 2>&1 || \
   [ -z "$(docker compose ps postgres --status running -q 2>/dev/null)" ]; then
    echo -e "${YELLOW}⚠ Postgres not running — starting it via docker compose...${NC}"
    docker compose up -d postgres
fi
echo -n "  Waiting for Postgres..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U trustchain -d trustchain >/dev/null 2>&1; then
        echo -e " ${GREEN}ready${NC}"
        break
    fi
    sleep 1
done

# ── Ensure Redis is up (SSE run-event bus + rate limiting — run_events.py, ──
# rate_limit.py. Without this, /run-agent's pipeline still executes but its
# background task's publish_event() calls throw a real ConnectionError the
# moment the first step tries to stream, killing the run — found by
# actually running a pipeline against this script, not by inspection.) ────
if ! docker compose ps redis --status running >/dev/null 2>&1 || \
   [ -z "$(docker compose ps redis --status running -q 2>/dev/null)" ]; then
    echo -e "${YELLOW}⚠ Redis not running — starting it via docker compose...${NC}"
    docker compose up -d redis
fi
echo -n "  Waiting for Redis..."
for i in $(seq 1 30); do
    if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
        echo -e " ${GREEN}ready${NC}"
        break
    fi
    sleep 1
done

# ── Run pending migrations (schema must exist before anything reads it) ──
echo -n "  Applying migrations..."
source "$VENV"
(cd "$BACKEND" && DATABASE_URL="postgresql+asyncpg://trustchain:trustchain@localhost:5432/trustchain" alembic upgrade head) \
    > "$ROOT/.logs/alembic.log" 2>&1 && echo -e " ${GREEN}done${NC}" \
    || { echo -e " ${RED}failed — see .logs/alembic.log${NC}"; exit 1; }

# Anvil's well-known default account #0 — public, funded only on local
# chains anvil itself spins up. DeployV2.s.sol grants this account
# ANCHOR_ROLE when RELAYER_ADDRESS is left unset (the local-dev default).
# Used below for the deploy itself, plus as the V2/anchor-worker signing
# key — NOT for MONAD_RPC_URL/PRIVATE_KEY, which stay whatever backend/.env
# has (the real V1 testnet bridge behind /verify etc.) — same split
# docker-compose.yml's own services make, see its comments.
ANVIL_RPC="http://localhost:8545"
ANVIL_KEY="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

# ── Ensure Anvil is up (local chain V2 contracts deploy to) ────────
if ! docker compose ps anvil --status running >/dev/null 2>&1 || \
   [ -z "$(docker compose ps anvil --status running -q 2>/dev/null)" ]; then
    echo -e "${YELLOW}⚠ Anvil not running — starting it via docker compose...${NC}"
    docker compose up -d anvil
fi
echo -n "  Waiting for Anvil..."
for i in $(seq 1 30); do
    if curl -sf -X POST -H "Content-Type: application/json" \
        --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
        "$ANVIL_RPC" 2>/dev/null | grep -q result; then
        echo -e " ${GREEN}ready${NC}"
        break
    fi
    sleep 1
done

# ── Deploy V2 contracts if this Anvil doesn't already have them ────
# Anvil has no persistent volume — a container that got recreated (not just
# restarted) resets to block 0, wiping any previously deployed V2 contracts
# even though addresses_v2.json on disk still points at the old (now-empty)
# addresses. CREATE addresses are deterministic given the same deployer key
# + a fresh chain, so redeploying is safe and idempotent EXCEPT when Anvil
# is still the same running instance from last time (state persisted) — in
# that case a second `forge script --broadcast` would just deploy a second
# generation at new addresses and TrustChainRegistry.getCurrentDeployment()
# would still correctly point at the latest one, but it's wasted work and
# churns addresses_v2.json for no reason. So: check whether the registry
# address already on disk actually has bytecode on THIS Anvil before
# bothering to redeploy at all.
ADDR_FILE="$BACKEND/contracts/addresses_v2.json"
NEEDS_DEPLOY=1
if [ -f "$ADDR_FILE" ]; then
    REGISTRY_ADDR=$(python3 -c "import json; print(json.load(open('$ADDR_FILE')).get('TrustChainRegistry',''))" 2>/dev/null || true)
    if [ -n "$REGISTRY_ADDR" ]; then
        CODE=$(curl -s -X POST -H "Content-Type: application/json" \
            --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getCode\",\"params\":[\"$REGISTRY_ADDR\",\"latest\"],\"id\":1}" \
            "$ANVIL_RPC" 2>/dev/null)
        if ! echo "$CODE" | grep -q '"result":"0x"'; then
            NEEDS_DEPLOY=0
        fi
    fi
fi

if [ "$NEEDS_DEPLOY" -eq 1 ]; then
    echo -e "  ${YELLOW}⚠ V2 contracts not found on this Anvil — deploying...${NC}"
    (cd "$ROOT/contracts" && PRIVATE_KEY="$ANVIL_KEY" forge script script/DeployV2.s.sol \
        --rpc-url "$ANVIL_RPC" --broadcast) > "$ROOT/.logs/deploy_v2.log" 2>&1 \
        && echo -e "       ${GREEN}✓ deployed${NC}" \
        || { echo -e "       ${RED}✗ deploy failed — see .logs/deploy_v2.log${NC}"; exit 1; }
    python3 "$BACKEND/scripts/write_v2_addresses.py" --chain-id 31337
    echo -e "       ${DIM}addresses written to backend/contracts/addresses_v2.json${NC}"
else
    echo -e "  ${GREEN}✓ V2 contracts already deployed on this Anvil${NC} ${DIM}(addresses_v2.json unchanged)${NC}"
fi

echo -e "${BOLD}Starting services...${NC}"
echo ""

# ── 1. MCP web_search server ───────────────────────────────────────
echo -e "  ${CYAN}[1/6]${NC} MCP web_search server    ${DIM}→ localhost:8001${NC}"
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
echo -e "  ${CYAN}[2/6]${NC} MCP blockchain server     ${DIM}→ localhost:8002${NC}"
python "$MCP_CHAIN" > "$CHAIN_LOG" 2>&1 &
PIDS+=($!)
sleep 1

if kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
else
    echo -e "       ${RED}✗ failed to start — check .logs/mcp_blockchain.log${NC}"
fi

# ── 3. FastAPI backend ─────────────────────────────────────────────
# (migrations already applied earlier, against the docker-compose Postgres —
# see the "Applying migrations" step above)
#
# V2_RPC_URL/V2_PRIVATE_KEY override backend/.env's own V2_* (usually unset)
# so agents/scorer.py's on-chain score writes and POST /agents' identity
# registration go to the local Anvil we just deployed V2 to, not fall back
# to MONAD_RPC_URL/PRIVATE_KEY (the real V1 testnet bridge's creds) — see
# config.py's resolved_v2_rpc_url()/resolved_v2_private_key() fallback and
# the identical override docker-compose.yml's `api` service makes.
echo -e "  ${CYAN}[3/6]${NC} FastAPI backend           ${DIM}→ localhost:8000${NC}"
cd "$BACKEND"
V2_RPC_URL="$ANVIL_RPC" V2_PRIVATE_KEY="$ANVIL_KEY" \
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

# ── 4. Anchor worker ────────────────────────────────────────────────
# Claims pending anchor_outbox rows and submits anchorBatch() — without
# this running, steps get durably recorded (log_step's same-txn outbox
# write) but never actually make it on-chain. MONAD_RPC_URL/PRIVATE_KEY
# overridden the same way as docker-compose.yml's anchor-worker service
# (real .env values are the V1 testnet bridge's, not local Anvil's).
ANCHOR_LOG="$ROOT/.logs/anchor_worker.log"
echo -e "  ${CYAN}[4/6]${NC} Anchor worker             ${DIM}→ :9101/metrics${NC}"
# Direct launch, not a `(...) &` subshell — $! must be the actual python3
# PID or `kill "$pid"` in cleanup() above only kills an already-exited
# subshell wrapper on Ctrl+C, orphaning this process still running.
cd "$BACKEND"
MONAD_RPC_URL="$ANVIL_RPC" PRIVATE_KEY="$ANVIL_KEY" ANCHOR_MAX_BATCH_AGE_SECONDS="5" \
    python3 -m anchor_worker.main > "$ANCHOR_LOG" 2>&1 &
PIDS+=($!)
cd "$ROOT"
sleep 1
if kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
else
    echo -e "       ${RED}✗ failed to start — check .logs/anchor_worker.log${NC}"
fi

# ── 5. Indexer ───────────────────────────────────────────────────────
# Polls on-chain events the anchor worker submitted back into the read
# model — without this, /verify and the dashboard's anchor status never
# reflect confirmed batches even once the anchor worker does its job.
INDEXER_LOG="$ROOT/.logs/indexer.log"
echo -e "  ${CYAN}[5/6]${NC} Indexer                   ${DIM}→ :9102/metrics${NC}"
cd "$BACKEND"
MONAD_RPC_URL="$ANVIL_RPC" python3 -m indexer.main > "$INDEXER_LOG" 2>&1 &
PIDS+=($!)
cd "$ROOT"
sleep 1
if kill -0 "${PIDS[-1]}" 2>/dev/null; then
    echo -e "       ${GREEN}✓ running${NC} ${DIM}(PID ${PIDS[-1]})${NC}"
else
    echo -e "       ${RED}✗ failed to start — check .logs/indexer.log${NC}"
fi

# ── 6. Next.js frontend ────────────────────────────────────────────
echo -e "  ${CYAN}[6/6]${NC} Next.js frontend          ${DIM}→ localhost:3000${NC}"
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
echo -e "  ${BOLD}Anchor metrics${NC}${CYAN} http://localhost:9101/metrics${NC}"
echo -e "  ${BOLD}Indexer metrics${NC}${CYAN} http://localhost:9102/metrics${NC}"
echo ""
echo -e "  ${DIM}Logs:${NC}"
echo -e "  ${DIM}  MCP search    .logs/mcp_search.log${NC}"
echo -e "  ${DIM}  MCP chain     .logs/mcp_blockchain.log${NC}"
echo -e "  ${DIM}  FastAPI       .logs/fastapi.log${NC}"
echo -e "  ${DIM}  Anchor worker .logs/anchor_worker.log${NC}"
echo -e "  ${DIM}  Indexer       .logs/indexer.log${NC}"
echo -e "  ${DIM}  Frontend      .logs/frontend.log${NC}"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services${NC}"
echo ""

# ── Tail FastAPI log so you see agent output live ──────────────────
tail -f "$API_LOG" &
PIDS+=($!)

# ── Keep alive ─────────────────────────────────────────────────────
wait