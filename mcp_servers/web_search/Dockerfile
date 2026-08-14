# web_search MCP server — self-contained (no backend/ dependency), unlike
# mcp_servers/blockchain (see that Dockerfile's comment on why its build
# context has to be the repo root instead of just its own directory).

FROM python:3.14-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.14-slim AS runtime

WORKDIR /app

RUN useradd --create-home --uid 1000 trustchain
COPY --from=deps /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY . .
RUN chown -R trustchain:trustchain /app
USER trustchain

EXPOSE 8001

CMD ["python3", "server.py"]
