# TrustChain API service.
#
# Matches the Python version used in local development (see .venv) so a
# "works on my machine" gap can't hide here. Separate stage for deps so
# `docker compose up` rebuilds are fast when only application code changes.

FROM python:3.14-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.14-slim AS runtime

WORKDIR /app

# Run as a non-root user — no reason the API process needs root inside the
# container, and it costs nothing to avoid it.
RUN useradd --create-home --uid 1000 trustchain
COPY --from=deps /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY . .
RUN chown -R trustchain:trustchain /app
USER trustchain

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
