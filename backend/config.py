"""
config.py — typed, validated application configuration.

Replaces scattered `os.getenv(...)` calls (each with its own ad hoc default
and no validation) across main.py / auth.py / db.py / blockchain/client.py /
agents/base.py with a single source of truth.

IMPORTANT: settings are instantiated lazily via get_settings(), never as a
bare module-level `settings = Settings()`. A bare module-level instance
would validate (and can fail closed — see JWT_SECRET) the moment this
module is imported, which happens transitively via `import main` at the
top of every test file. That would make it impossible for tests (or any
caller) to set environment variables *before* validation runs. Every
consumer must call get_settings() rather than importing a top-level
constant.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────────────
    # Async DSN — must use the asyncpg driver (postgresql+asyncpg://...).
    database_url: str = Field(
        default="postgresql+asyncpg://trustchain:trustchain@localhost:5432/trustchain",
        description="Async SQLAlchemy connection string for Postgres.",
    )
    # Tests mix asyncio.run() (a fresh event loop per call) with requests
    # driven through FastAPI's TestClient (which keeps one loop alive for
    # the whole `with TestClient(app) as c:` block). asyncpg connections are
    # bound to the loop that created them, so a *pooled* connection handed
    # out under one loop and reused under another raises "attached to a
    # different loop". NullPool opens a fresh low-level connection per
    # checkout and never holds one between calls, sidestepping that
    # entirely — at the cost of connection-reuse efficiency, irrelevant at
    # test scale. Never set this outside tests; production has exactly one
    # event loop for the process lifetime, so pooling is safe and wanted.
    database_use_null_pool: bool = Field(default=False)

    # ── Redis (SSE pub/sub across API replicas, rate limiting) ────────────
    # F5 in the Phase 2 plan's fix list: in-process SSE queues make a
    # second API replica silently break streaming (each replica only sees
    # the runs its own process started). Redis Streams fix that — see
    # main.py's run_events module.
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── Auth ─────────────────────────────────────────────────────────────
    # No default. Missing JWT_SECRET fails startup instead of silently
    # generating a random per-process secret (Phase 1 behavior) — a random
    # secret invalidates every session on restart and differs across
    # replicas, which is exactly the kind of "looks fine in a demo, breaks
    # in production" bug this phase exists to remove.
    jwt_secret: str = Field(...)

    # ── LLM / search providers ──────────────────────────────────────────
    # "groq" is the only implementation today — see
    # agents/llm_provider.py's module docstring for why this is a config
    # value at all rather than a hardcoded import (F17, Phase 2 plan's
    # fix list): failover to a second provider is a config change plus
    # one new branch in build_chat_model(), not a rewrite of every agent
    # node, since they only ever talk to LangChain's common BaseChatModel
    # interface.
    llm_provider: str = Field(default="groq")
    groq_api_key: str = Field(default="")
    tavily_api_key: str = Field(default="")

    # ── MCP servers ──────────────────────────────────────────────────────
    # Defaults match start.sh's local (non-Docker) dev setup, where both
    # servers run as plain processes on localhost. Inside Docker Compose,
    # "localhost" resolves to the api container itself, not a sibling
    # container — the api service overrides these to the mcp-search/
    # mcp-blockchain service names there (see docker-compose.yml).
    mcp_search_url:     str = Field(default="http://localhost:8001/mcp")
    mcp_blockchain_url: str = Field(default="http://localhost:8002/mcp")

    # ── Blockchain ───────────────────────────────────────────────────────
    monad_rpc_url: str = Field(default="https://testnet-rpc.monad.xyz")
    private_key: str = Field(default="")  # dev-only signer; see blockchain/signer.py

    # V1 (blockchain/client.py's BlockchainBridge) and V2 (anchor_worker/,
    # indexer/, scorer.py's score writes) can legitimately point at
    # different chains during this migration: local dev deploys V2 fresh
    # to Anvil (contracts/addresses_v2.json) while V1 stays wherever it
    # already was (real Monad testnet, per .env) since it was never
    # redeployed there. Single-purpose containers (anchor-worker, indexer)
    # only ever touch V2, so overriding monad_rpc_url/private_key directly
    # works fine for them — these v2_* fields exist for the `api` process,
    # which needs V1 and V2 simultaneously and can't let one override clobber
    # the other. Empty (the default) means "same as V1's" — once V2 fully
    # replaces V1 in a real deployment, both point at the same place and
    # these never need to be set.
    v2_rpc_url: str = Field(default="")
    v2_private_key: str = Field(default="")

    # ── Signing backend (P2.5) ───────────────────────────────────────────
    # "local" (default) reads v2_private_key/private_key straight into
    # process memory — fine for Anvil/testnet, never for a production key
    # with real value behind it. "aws_kms" / "gcp_kms" keep the key inside
    # the respective cloud KMS at all times; see blockchain/signer.py's
    # AwsKmsSigner/GcpKmsSigner. kms_key_id is the KMS key id (AWS) or full
    # CryptoKeyVersion resource path (GCP) — required for either KMS backend.
    signer_backend: str = Field(default="local")
    kms_key_id: str = Field(default="")
    kms_region: Optional[str] = Field(default=None)  # AWS only; GCP resolves region from kms_key_id

    @field_validator("signer_backend")
    @classmethod
    def _validate_signer_backend(cls, v: str) -> str:
        if v not in ("local", "aws_kms", "gcp_kms"):
            raise ValueError(f"signer_backend must be 'local', 'aws_kms', or 'gcp_kms' (got {v!r})")
        return v

    # ── CORS ─────────────────────────────────────────────────────────────
    frontend_url: Optional[str] = Field(default=None)

    # ── Anchoring / outbox (Phase 2.1+, referenced here so config stays
    #    centralized even though the consumers land in later phases) ─────
    anchor_max_batch_size: int = Field(default=256, ge=1)
    anchor_claim_timeout_seconds: int = Field(default=120, ge=1)
    anchor_poll_interval_seconds: float = Field(default=2.0, gt=0)
    anchor_max_attempts: int = Field(default=8, ge=1)

    # ── Indexer (Phase 2.2) ──────────────────────────────────────────────
    indexer_poll_interval_seconds: float = Field(default=2.0, gt=0)

    # ── Rate limiting (Phase 2.3) ────────────────────────────────────────
    # POST /run-agent spends both LLM credits and gas — real money — so it
    # gets its own, stricter bucket than general reads. Values are a
    # starting point (5 runs/min per project, refilling continuously),
    # tunable per deployment without a code change.
    run_agent_rate_limit_capacity: float = Field(default=5.0, gt=0)
    run_agent_rate_limit_refill_per_second: float = Field(default=5.0 / 60, gt=0)
    monthly_run_quota_per_org: int = Field(default=1000, ge=0)

    # ── Observability (P2.6) ─────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")

    # Empty (default) disables Sentry entirely — sentry_sdk.init() is
    # simply never called, so there's zero behavior change (and zero
    # network calls) for anyone who hasn't set this.
    sentry_dsn: str = Field(default="")
    # Fraction of transactions sampled for Sentry performance tracing —
    # kept low by default since this is a demo/small-scale deployment,
    # not because sampling matters for correctness.
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    # Empty (default) disables OpenTelemetry span export — spans are still
    # created (cheap, no-op without a configured exporter) but never sent
    # anywhere. Point this at a local collector (e.g. Jaeger's OTLP gRPC
    # endpoint, localhost:4317) for real tracing.
    otel_exporter_otlp_endpoint: str = Field(default="")
    otel_service_name: str = Field(default="trustchain-api")

    # Port each process's Prometheus /metrics endpoint listens on. The API
    # exposes its own via FastAPI's router (same port as the app); the
    # anchor worker and indexer are plain asyncio loops with no HTTP
    # server of their own, so they each start a tiny dedicated one here.
    anchor_worker_metrics_port: int = Field(default=9101, ge=1, le=65535)
    indexer_metrics_port: int = Field(default=9102, ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            raise ValueError(
                "database_url must use the asyncpg driver: "
                "postgresql+asyncpg://... (got postgresql://...)"
            )
        return v

    @property
    def resolved_v2_rpc_url(self) -> str:
        return self.v2_rpc_url or self.monad_rpc_url

    @property
    def resolved_v2_private_key(self) -> str:
        return self.v2_private_key or self.private_key


@lru_cache
def get_settings() -> Settings:
    """
    Cached singleton accessor. Use this everywhere instead of constructing
    Settings() directly, so (a) validation happens once, lazily, on first
    real use rather than at import time, and (b) tests can monkeypatch
    environment variables before the first call and get a fresh instance
    by clearing the cache (get_settings.cache_clear()).
    """
    return Settings()
