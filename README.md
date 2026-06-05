# TrustChain

> Multi-agent AI with every step permanently recorded on Monad testnet.

## What it does
A judge opens the app, types a task, watches 4 AI agents run live,
and sees every step written as a transaction on Monad blockchain.
Each tx hash is clickable — immutable proof nothing was tampered with.

## Architecture
- **Frontend** — Next.js 14, shadcn/ui, Recharts, SSE live feed
- **Backend**  — FastAPI + LangGraph 4-agent pipeline (Groq LLM + Tavily)
- **Blockchain** — 3 Solidity contracts on Monad testnet (web3.py bridge)

## Contracts (Monad Testnet)
| Contract | Address |
|---|---|
| AgentAuditLog | `0xcf15079dbf148205516aee935c3cc5cdd4ceb4b9` |
| TrustScoreRegistry | `0xc0e3ab853587e0bb039249ef47aade7b055c58fd` |
| AgentIdentityRegistry | `0x68f7fd16e99b640cb7b9a957ac12b4f13fa792ed` |

## Quick Start
\`\`\`bash
# Backend
cd backend
cp .env.example .env   # fill in keys
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
\`\`\`

## Repo Structure
\`\`\`
contracts/   Solidity + Foundry deployment
backend/     FastAPI + LangGraph agents + web3.py bridge
frontend/    Next.js app (coming next)
\`\`\`