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
    # iss/aud are validated on every decode (see auth.decode_token) so a
    # token minted for a different TrustChain deployment/environment that
    # happens to share this JWT_SECRET (e.g. a staging secret reused by
    # accident) is rejected rather than silently accepted as valid here.
    jwt_issuer: str = Field(default="trustchain-api")
    jwt_audience: str = Field(default="trustchain-clients")

    # ── Credential-stuffing defence (plan §11.3) ─────────────────────────
    # "optional breach check against Have I Been Pwned using the k-
    # anonymity range API" — only the SHA-1 hash's first 5 hex chars ever
    # leave this process (see auth_pwned.py), never the password or its
    # full hash, so this can't leak a candidate password to the third
    # party it's checking against. Default on, but a real toggle (not just
    # documentation) — an air-gapped/offline deployment, or one that
    # can't depend on a third-party API's uptime for signup to work at
    # all, can turn it off outright rather than relying on the fail-open
    # behavior alone.
    check_pwned_passwords: bool = Field(default=True)
    # A fresh httpx.AsyncClient is created per check (no persistent
    # connection pool — signup isn't hot enough to justify one), so every
    # call pays full DNS + TLS-handshake cost, not just the request
    # itself. Repeatedly observed empirically: isolated, this check
    # reliably completes in under 3s; run as part of the full ~170-test
    # suite (many tests concurrently hammering real Postgres/Redis/Anvil
    # for host CPU/network), it occasionally exceeded even a 5s timeout
    # (httpx.ConnectTimeout/ReadTimeout, not an HIBP outage — reproduced
    # the same call in isolation immediately after and it succeeded in
    # under a second). 8s comfortably absorbs that contention without
    # being long enough to make a genuine outage look like a hung signup.
    pwned_passwords_timeout_seconds: float = Field(default=8.0, gt=0)

    # ── Graceful shutdown (F14) ──────────────────────────────────────────
    # On SIGTERM, main.py's lifespan waits up to this long for any
    # in-flight POST /run-agent background pipeline task to finish
    # naturally before force-marking it failed in Postgres (see lifespan's
    # own docstring) — "drain in-flight work on SIGTERM; never abandon a
    # ...transaction without recording it" (plan F14). 5s is short by
    # design: a pipeline run can legitimately take minutes
    # (PIPELINE_RUN_DURATION_SECONDS's histogram buckets go up to 600s),
    # far too long to block a deploy on — this bounds the WAIT, not the
    # run itself; anything not done in time gets a recorded, explicit
    # "interrupted by shutdown" failure instead of hanging at
    # status='running' forever with no explanation.
    api_shutdown_drain_timeout_seconds: float = Field(default=5.0, gt=0)

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
    # Comma-separated additional RPC endpoints, tried in order after
    # monad_rpc_url/v2_rpc_url when the primary's circuit breaker is open —
    # see blockchain/resilient_provider.py. Empty (the default) means
    # single-endpoint, no failover, exactly today's behavior.
    monad_rpc_fallback_urls: str = Field(default="")
    v2_rpc_fallback_urls: str = Field(default="")

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
    # "vault_kv" fetches the raw key from HashiCorp Vault's KV v2 engine at
    # startup instead of a plaintext .env file — see VaultKvSigner's own
    # docstring for why this is Vault-backed key CUSTODY, not Vault-backed
    # SIGNING like the two KMS backends above (Vault OSS's Transit engine,
    # the actual equivalent, doesn't support secp256k1).
    signer_backend: str = Field(default="local")
    kms_key_id: str = Field(default="")
    kms_region: Optional[str] = Field(default=None)  # AWS only; GCP resolves region from kms_key_id
    vault_addr: str = Field(default="")
    vault_token: str = Field(default="")
    vault_secret_path: str = Field(default="")
    vault_mount_point: str = Field(default="secret")
    vault_key_field: str = Field(default="private_key")

    @field_validator("signer_backend")
    @classmethod
    def _validate_signer_backend(cls, v: str) -> str:
        if v not in ("local", "aws_kms", "gcp_kms", "vault_kv"):
            raise ValueError(f"signer_backend must be 'local', 'aws_kms', 'gcp_kms', or 'vault_kv' (got {v!r})")
        return v

    # ── CORS ─────────────────────────────────────────────────────────────
    frontend_url: Optional[str] = Field(default=None)

    # ── Anchoring / outbox (Phase 2.1+, referenced here so config stays
    #    centralized even though the consumers land in later phases) ─────
    anchor_max_batch_size: int = Field(default=256, ge=1)
    anchor_claim_timeout_seconds: int = Field(default=120, ge=1)
    anchor_poll_interval_seconds: float = Field(default=2.0, gt=0)
    anchor_max_attempts: int = Field(default=8, ge=1)
    # A failed submit's requeued outbox rows get next_attempt_at = now +
    # min(backoff_base * 2^(attempts-1), backoff_max) — not an immediate
    # retry. A transient failure (RPC hiccup, momentary insufficient
    # funds) retried instantly just re-fails instantly too, burning
    # attempts against ANCHOR_MAX_ATTEMPTS for no benefit; backoff gives
    # whatever caused the failure real time to clear before trying again.
    anchor_backoff_base_seconds: float = Field(default=5.0, gt=0)
    anchor_backoff_max_seconds: float = Field(default=300.0, gt=0)
    # Batches also flush when the oldest pending outbox row has been
    # waiting this long, not just when batch_size is reached — otherwise
    # low traffic (a trickle of steps that never fills a full batch)
    # waits indefinitely for anchoring with no latency bound. 0 (ge=0,
    # not gt=0 like the backoff fields above) is a deliberately valid
    # value meaning "no accumulation delay at all, flush every poll
    # cycle" — `now - oldest >= 0` is always true since age can never be
    # negative, unlike a small-but-positive threshold which can still
    # fail to trigger for a row created in the same integer-second
    # timestamp as `now` (found while wiring this up: tests seed a step
    # and immediately call run_once(), often under 1s later).
    anchor_max_batch_age_seconds: float = Field(default=30.0, ge=0)
    # A submitted anchor tx that doesn't confirm within confirm_timeout
    # isn't necessarily reverted — it may just be underpriced for current
    # network conditions and sitting in the mempool. Abandoning it (as a
    # plain SubmitError would) and submitting a fresh tx at the next nonce
    # doesn't fix that: the fresh tx queues behind the still-pending one at
    # the earlier nonce and can never mine until that one resolves one way
    # or the other, potentially blocking the signer indefinitely. Instead,
    # submit_batch resubmits at the SAME nonce with a bumped fee (standard
    # "speed up" / replace-by-fee) up to anchor_rbf_max_attempts times
    # before finally giving up.
    anchor_rbf_max_attempts: int = Field(default=3, ge=1)
    anchor_rbf_fee_bump_fraction: float = Field(default=0.15, gt=0)

    # ── Indexer (Phase 2.2) ──────────────────────────────────────────────
    indexer_poll_interval_seconds: float = Field(default=2.0, gt=0)
    # Finality gating: the indexer only scans up to (chain tip -
    # finality_confirmations), never the raw tip itself. cursor.py's
    # hash-comparison reorg detection is REACTIVE — it catches a reorg
    # and rewinds only after already having indexed blocks that later got
    # orphaned. This is the complementary PROACTIVE half: waiting for N
    # confirmations before indexing a block at all means most reorgs (an
    # Anvil/Monad single-sequencer chain reorganizing only its most recent
    # few blocks) never get indexed in the first place, so the reactive
    # path has less to clean up. 0 (the default) means no gating —
    # appropriate for local Anvil dev, where there are no other nodes to
    # reorg against; real Monad testnet deployments should set this above
    # 0. Not `gt=0`: 0 is a real, valid "finality gating disabled" value,
    # not a placeholder.
    indexer_finality_confirmations: int = Field(default=0, ge=0)

    # ── Rate limiting (Phase 2.3) ────────────────────────────────────────
    # POST /run-agent spends both LLM credits and gas — real money — so it
    # gets its own, stricter bucket than general reads. Values are a
    # starting point (5 runs/min per project, refilling continuously),
    # tunable per deployment without a code change.
    run_agent_rate_limit_capacity: float = Field(default=5.0, gt=0)
    run_agent_rate_limit_refill_per_second: float = Field(default=5.0 / 60, gt=0)
    monthly_run_quota_per_org: int = Field(default=1000, ge=0)
    # POST /agents (register_agent) spends real gas on every call, same
    # as /run-agent — was completely unlimited until this field existed
    # (found while auditing which write endpoints had NO rate limit at
    # all despite spending money). Rarer than /run-agent in normal usage
    # (once per agent config change, not once per pipeline run), so a
    # smaller bucket is appropriate.
    register_agent_rate_limit_capacity: float = Field(default=10.0, gt=0)
    register_agent_rate_limit_refill_per_second: float = Field(default=10.0 / 60, gt=0)
    # POST /steps (log_external_step) doesn't spend gas synchronously
    # (steps go through the outbox + anchor worker's batching, same as
    # the internal pipeline's own steps), but it's still unbounded
    # Postgres write volume from an unauthenticated-cost perspective —
    # a caller could otherwise flood the outbox/anchor-worker backlog
    # (see AnchorOutboxBacklogGrowing, docker/prometheus/alerts.yml) for
    # free. Generous relative to run-agent since one real agent run
    # legitimately logs many steps.
    log_step_rate_limit_capacity: float = Field(default=120.0, gt=0)
    log_step_rate_limit_refill_per_second: float = Field(default=120.0 / 60, gt=0)

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

    @staticmethod
    def _parse_fallback_urls(raw: str) -> list[str]:
        return [url.strip() for url in raw.split(",") if url.strip()]

    @property
    def resolved_v2_rpc_urls(self) -> list[str]:
        """Primary + fallbacks, in try-order, de-duplicated. A single-item
        list (today's default) makes blockchain/resilient_provider.py's
        FallbackHTTPProvider behave exactly like a plain HTTPProvider —
        the resilience layer is opt-in via config, not a behavior change
        for anyone who hasn't set *_RPC_FALLBACK_URLS."""
        urls = [self.resolved_v2_rpc_url, *self._parse_fallback_urls(self.v2_rpc_fallback_urls)]
        return list(dict.fromkeys(urls))

    @property
    def resolved_monad_rpc_urls(self) -> list[str]:
        urls = [self.monad_rpc_url, *self._parse_fallback_urls(self.monad_rpc_fallback_urls)]
        return list(dict.fromkeys(urls))

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
