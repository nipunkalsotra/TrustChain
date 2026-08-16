# trustchain-cli

Command-line tool for TrustChain: verify a run's audit trail, manage
agent identities and API keys, and drive a local dev stack — all
without leaving the terminal.

This is a separate PyPI package from `trustchain-sdk` (it depends on
it as a library), matching the plan's package table: `trustchain-sdk`
(Python), `trustchain-sdk` (TypeScript, `sdk/typescript`), and
`trustchain-cli` (this package). The TypeScript SDK also ships its own
embedded CLI (`node dist/cli.js`, see `sdk/typescript/README.md`) that
drives TrustChain's own pipeline; this package is the standalone
Python binary and additionally covers account/credential management
(`login`, `keys`) and local-proof verification that the embedded CLI
doesn't.

```bash
cd sdk/python-cli
pip install -e ".[dev]"   # also installs trustchain-sdk as a dependency
```

## Credential resolution

Every command needs a credential — either a session JWT (from
`trustchain login`) or an API key (`tc_live_.../tc_test_...`, minted
via `trustchain keys create`). Resolved in this order:

1. `--token`/`--api-key` flag (must come **before** the subcommand,
   e.g. `trustchain --api-key tc_... runs list`, not after — standard
   argparse subparser behavior)
2. `TRUSTCHAIN_TOKEN`/`TRUSTCHAIN_API_KEY` environment variable
3. The token cached by `trustchain login` (`~/.trustchain/credentials.json`,
   override the directory with `TRUSTCHAIN_CONFIG_DIR`)

Most commands accept either kind of credential interchangeably — the
backend's `get_current_principal` dependency does too. `trustchain keys
*` is the one exception: minting or listing API keys requires a real
login session (JWT), not an API key, so a key can never mint more keys.

`--base-url` (default `http://localhost:8000`) follows the same
priority, falling back to `TRUSTCHAIN_BASE_URL` then the base URL
cached at login time.

## Commands

```bash
# Auth
trustchain login you@example.com          # prompts for password, caches the JWT
trustchain logout

# API keys (needs a login session, not an API key)
trustchain keys create --scopes runs:read,runs:write [--environment live|test]
trustchain keys list
trustchain keys revoke <key-id>

# Runs
trustchain runs list [--limit N]
trustchain runs get <run-id>

# Verify every anchored step's Merkle proof for a run — LOCAL recompute,
# doesn't trust the API's own word for it beyond leaf/proof/root values
trustchain verify <run-id>

# Agents
trustchain agents list [--include-revoked]   # read model (Postgres, kept in sync by the indexer), not a live chain call
trustchain agents register <agent-id> --model M --version V --system-prompt "..."   # real on-chain write, AgentIdentityRegistryV2
trustchain agents verify <agent-id> --model M --version V --system-prompt "..."     # real on-chain read, AgentIdentityRegistryV2

# Local dev stack (wraps docker compose; finds docker-compose.yml by
# searching upward from cwd, same as how git finds .git)
trustchain dev up
trustchain dev down
trustchain dev status
trustchain dev logs [service]
```

### `trustchain verify` — the flagship command

```bash
$ trustchain verify sdk_support-bot_a1b2c3d4
  VERIFIED step 3 (support-bot/answer_query) — tx=0x56e1be10...
  VERIFIED step 4 (support-bot/answer_query) — tx=0x56e1be10...

All 2 step(s) for run 'sdk_support-bot_a1b2c3d4' verified.
```

Fetches the run's audit-log entries, fetches each anchored step's
Merkle proof (`GET /steps/{id}/proof`), and verifies each one locally
via `trustchain_sdk.merkle.verify_proof` — the same pure computation
the SDK itself uses, not a re-implementation that happens to usually
agree with it. A step that isn't anchored yet prints `PENDING`; a
step whose proof fails to verify prints `FAILED`. Exits non-zero if
any step isn't `VERIFIED`.

## Testing

`tests/test_cli.py` runs against a REAL TrustChain stack — no mocking
of `httpx` or subprocess calls to `docker compose`:

```bash
docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
pip install -e "../python" -e ".[dev]"
pytest tests/test_cli.py -v
```

The on-chain tests (`agents register`/`agents verify`, the full
`verify <run-id>` round-trip) additionally need Anvil with V2 deployed
and are skipped otherwise. Every test isolates its own
`TRUSTCHAIN_CONFIG_DIR` so none of them touch your real cached login.
