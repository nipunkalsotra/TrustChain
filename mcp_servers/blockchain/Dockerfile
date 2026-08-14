# blockchain MCP server — wraps backend/blockchain/client.py's
# BlockchainBridge (see server.py's sys.path hack, which expects to find a
# `backend/` directory two levels up from itself). That means, unlike
# web_search's Dockerfile, this one needs the REPO ROOT as its build
# context, not just mcp_servers/blockchain/ — build with:
#   docker build -f mcp_servers/blockchain/Dockerfile .
# (docker-compose.yml's mcp-blockchain service sets this up already.)
#
# backend/requirements.txt is a strict superset of this server's own
# requirements.txt (same fastmcp/web3/python-dotenv versions) — installing
# it once covers both what this server needs directly and what the
# backend/blockchain package it imports needs transitively.

FROM python:3.14-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.14-slim AS runtime

WORKDIR /app

RUN useradd --create-home --uid 1000 trustchain
COPY --from=deps /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY backend/ ./backend/
COPY mcp_servers/blockchain/ ./mcp_servers/blockchain/
RUN chown -R trustchain:trustchain /app
USER trustchain

EXPOSE 8002

CMD ["python3", "mcp_servers/blockchain/server.py"]
