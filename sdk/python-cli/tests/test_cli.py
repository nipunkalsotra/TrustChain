"""tests/test_cli.py — integration tests for the `trustchain` CLI, against
a REAL running API + real Anvil with V2 deployed — no mocking.

Run:
    docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
    pip install -e "../python" -e ".[dev]"
    pytest tests/test_cli.py -v
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from trustchain_cli import credentials
from trustchain_cli.main import build_parser

BASE_URL = "http://localhost:8000"
ANVIL_RPC = "http://localhost:8545"

# Reaches into backend/ directly for the verification step — same
# deliberate, narrow exception to this suite's real-HTTP-only philosophy
# as sdk/python/tests/conftest.py::verified_signup and
# sdk/typescript/tests/testHelpers.ts::verifiedSignup, mirrored here
# because this suite never got the equivalent Phase 4 G1 fix those two
# did: every _fresh_user_credentials()/_fresh_api_key() call site 403'd
# with email_not_verified against the real Phase-4-patched backend (13 of
# 16 tests in this file) until this was added.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
_BACKEND_PATH = str(_BACKEND_DIR)
if _BACKEND_PATH not in sys.path:
    sys.path.insert(0, _BACKEND_PATH)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(_BACKEND_DIR / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)


def _mark_verified(email: str) -> None:
    asyncio.run(_mark_verified_async(email))


async def _mark_verified_async(email: str) -> None:
    import asyncpg
    from config import get_settings

    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET email_verified = true WHERE email = $1", email)
    finally:
        await conn.close()


def _tamper_step_output_hash(step_id: int) -> None:
    """Same raw-SQL tamper as sdk/python/tests/conftest.py::
    tamper_step_output_hash and sdk/typescript/tests/testHelpers.ts::
    tamperStepOutputHash — needed here for the identical reason: the
    'after tampering' path of `trustchain integrity verify-content` only
    has anything real to prove once a step has actually been edited
    post-hoc, and there is no legitimate HTTP endpoint that does that
    (by design — see docs/adr/0020)."""
    asyncio.run(_tamper_step_output_hash_async(step_id))


async def _tamper_step_output_hash_async(step_id: int) -> None:
    import asyncpg
    from config import get_settings

    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE steps SET output_hash = '0x' || repeat('f', 64) WHERE id = $1", step_id,
        )
    finally:
        await conn.close()


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _anvil_is_up() -> bool:
    try:
        r = httpx.post(ANVIL_RPC, json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}, timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _stack_is_up(), reason=f"no TrustChain API reachable at {BASE_URL}")
requires_anvil = pytest.mark.skipif(not _anvil_is_up(), reason=f"no Anvil reachable at {ANVIL_RPC}")


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Every test gets its own throwaway ~/.trustchain — never touches
    the real developer's cached login."""
    monkeypatch.setenv("TRUSTCHAIN_CONFIG_DIR", str(tmp_path / ".trustchain"))
    monkeypatch.delenv("TRUSTCHAIN_TOKEN", raising=False)
    monkeypatch.delenv("TRUSTCHAIN_API_KEY", raising=False)


def _run(argv: list[str]):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _fresh_user_credentials() -> tuple[str, str]:
    """Returns (email, password) for a freshly signed-up user."""
    email = f"cli_test_{uuid.uuid4().hex}@example.com"
    password = "cli-test-password-123"
    signup = httpx.post(
        f"{BASE_URL}/auth/signup",
        json={"name": "cli test", "email": email, "password": password},
        timeout=10.0,
    )
    assert signup.status_code == 200, signup.text
    _mark_verified(email)
    return email, password


def _fresh_api_key(scopes: list[str]) -> str:
    email = f"cli_test_{uuid.uuid4().hex}@example.com"
    signup = httpx.post(
        f"{BASE_URL}/auth/signup",
        json={"name": "cli test", "email": email, "password": "cli-test-password-123"},
        timeout=10.0,
    )
    assert signup.status_code == 200, signup.text
    _mark_verified(email)
    token = signup.json()["token"]
    created = httpx.post(
        f"{BASE_URL}/api-keys",
        json={"scopes": scopes, "environment": "test"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert created.status_code == 200, created.text
    return created.json()["raw_key"]


# ── credential resolution ────────────────────────────────────────────────

def test_no_credential_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run(["runs", "list"])
    assert exc_info.value.code != 0
    assert "no credential" in capsys.readouterr().err


def test_explicit_api_key_flag_takes_priority_over_env(monkeypatch):
    monkeypatch.setenv("TRUSTCHAIN_API_KEY", "tc_test_wrong_key_from_env_00000000")
    key = _fresh_api_key(["runs:read"])
    # A wrong env-var key would 401; the correct --api-key flag should win.
    _run(["--api-key", key, "runs", "list"])


def test_env_var_used_when_no_flag(monkeypatch):
    key = _fresh_api_key(["runs:read"])
    monkeypatch.setenv("TRUSTCHAIN_API_KEY", key)
    _run(["runs", "list"])


# ── login / logout / keys ────────────────────────────────────────────────

def test_login_caches_token_then_keys_roundtrip(capsys):
    email, password = _fresh_user_credentials()
    _run(["login", email, "--password", password])
    assert credentials.load_token() is not None
    capsys.readouterr()

    _run(["keys", "create", "--scopes", "runs:read,runs:write"])
    created_out = capsys.readouterr().out
    assert "Created key" in created_out

    _run(["keys", "list"])
    listed_out = capsys.readouterr().out
    assert "runs:read" in listed_out

    import re
    key_id = re.search(r'"id": (\d+)', listed_out).group(1)
    _run(["keys", "revoke", key_id])
    assert "Revoked" in capsys.readouterr().out


def test_logout_clears_cached_token():
    email, password = _fresh_user_credentials()
    _run(["login", email, "--password", password])
    assert credentials.load_token() is not None

    _run(["logout"])
    assert credentials.load_token() is None


def test_login_with_wrong_password_exits_nonzero(capsys):
    email, _ = _fresh_user_credentials()
    with pytest.raises(SystemExit) as exc_info:
        _run(["login", email, "--password", "not-the-right-password"])
    assert exc_info.value.code != 0
    assert "login failed" in capsys.readouterr().err


# ── runs ──────────────────────────────────────────────────────────────────

def test_runs_list(capsys):
    key = _fresh_api_key(["runs:read"])
    _run(["--api-key", key, "runs", "list", "--limit", "5"])
    assert '"runs"' in capsys.readouterr().out


def test_runs_get_unknown_run_exits_nonzero(capsys):
    key = _fresh_api_key(["runs:read"])
    with pytest.raises(SystemExit):
        _run(["--api-key", key, "runs", "get", "run_does_not_exist"])
    assert "error" in capsys.readouterr().err


# ── agents ──────────────────────────────────────────────────────────────

def test_agents_list(capsys):
    # Read-model only (db/read_model.py::list_agents) — no Anvil needed,
    # unlike register/verify below which do a real on-chain call. This
    # stack's own DB persists real accumulated state across manual/CLI/k6
    # testing sessions (unlike backend/tests/, which gets a clean or
    # self-provisioned DB every run — see backend/tests/conftest.py), so
    # this only checks response SHAPE, not emptiness/count — see
    # test_agents_list_is_isolated_between_tenants below for the real
    # isolation check, done via two freshly-registered, uniquely-named
    # agents instead of assuming a clean starting state.
    key = _fresh_api_key(["agents:read"])
    _run(["--api-key", key, "agents", "list"])
    body = capsys.readouterr().out
    assert '"agents"' in body
    assert '"total"' in body


# ── agents — real on-chain ────────────────────────────────────────────────

@requires_anvil
def test_agents_register_then_verify(capsys):
    key = _fresh_api_key(["agents:register", "agents:read"])
    agent_id = f"cli_test_agent_{uuid.uuid4().hex[:8]}"

    _run([
        "--api-key", key, "agents", "register", agent_id,
        "--model", "gpt-4o", "--version", "2026-01", "--system-prompt", "You are helpful.",
    ])
    register_out = capsys.readouterr().out
    assert "Registered" in register_out

    _run([
        "--api-key", key, "agents", "verify", agent_id,
        "--model", "gpt-4o", "--version", "2026-01", "--system-prompt", "You are helpful.",
    ])
    verify_out = capsys.readouterr().out
    assert '"verified": true' in verify_out


@requires_anvil
def test_agents_list_is_isolated_between_tenants(capsys):
    # Real registrations under two DIFFERENT projects (not an assumption
    # about starting from an empty DB — this stack's own Postgres
    # accumulates real state across manual/CLI/k6 sessions, see
    # test_agents_list's comment) — proves invariant I7 (no tenant sees
    # another tenant's agents, docs/architecture.md) against THIS SDK
    # surface specifically, using agent_ids unique enough that a false
    # match against pre-existing data is not a realistic concern.
    key_a = _fresh_api_key(["agents:register", "agents:read"])
    key_b = _fresh_api_key(["agents:register", "agents:read"])
    agent_a = f"cli_isolation_test_a_{uuid.uuid4().hex[:8]}"
    agent_b = f"cli_isolation_test_b_{uuid.uuid4().hex[:8]}"

    _run([
        "--api-key", key_a, "agents", "register", agent_a,
        "--model", "gpt-4o", "--version", "1", "--system-prompt", "a",
    ])
    capsys.readouterr()
    _run([
        "--api-key", key_b, "agents", "register", agent_b,
        "--model", "gpt-4o", "--version", "1", "--system-prompt", "b",
    ])
    capsys.readouterr()

    import json

    def _agent_ids(key: str) -> set:
        _run(["--api-key", key, "agents", "list"])
        return {a["agentId"] for a in json.loads(capsys.readouterr().out)["agents"]}

    # The `agents` read model is populated by the indexer polling
    # on-chain events asynchronously (db/read_model.py::list_agents'
    # own docstring: "up to one indexer poll cycle behind") — a
    # freshly-registered agent isn't guaranteed to show up the instant
    # the register call returns, so poll with a real timeout rather than
    # asserting immediately.
    deadline = time.monotonic() + 15
    ids_a: set = set()
    while time.monotonic() < deadline:
        ids_a = _agent_ids(key_a)
        if agent_a in ids_a:
            break
        time.sleep(0.5)
    ids_b: set = set()
    while time.monotonic() < deadline:
        ids_b = _agent_ids(key_b)
        if agent_b in ids_b:
            break
        time.sleep(0.5)

    assert agent_a in ids_a
    assert agent_b not in ids_a
    assert agent_b in ids_b
    assert agent_a not in ids_b


@requires_anvil
def test_agents_verify_tampered_prompt_exits_nonzero(capsys):
    key = _fresh_api_key(["agents:register", "agents:read"])
    agent_id = f"cli_test_agent_{uuid.uuid4().hex[:8]}"
    _run([
        "--api-key", key, "agents", "register", agent_id,
        "--model", "gpt-4o", "--version", "2026-01", "--system-prompt", "Original.",
    ])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        _run([
            "--api-key", key, "agents", "verify", agent_id,
            "--model", "gpt-4o", "--version", "2026-01", "--system-prompt", "TAMPERED.",
        ])
    assert exc_info.value.code != 0
    assert '"verified": false' in capsys.readouterr().out


# ── verify <run-id> — the flagship command, full real Merkle round-trip ──

@requires_anvil
def test_verify_command_confirms_real_anchored_steps(capsys):
    from trustchain_sdk import TrustChain

    key = _fresh_api_key(["runs:read", "runs:write", "logs:write"])
    tc = TrustChain(key, base_url=BASE_URL, on_error="raise")
    agent_id = f"cli_test_agent_{uuid.uuid4().hex[:8]}"
    run_id = tc._current_run_id(agent_id)

    receipt = tc.log_and_wait(agent_id=agent_id, action="answer", input="q", output="a")
    assert receipt.step_id is not None

    deadline = time.time() + 30
    entries = []
    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/audit-log", params={"run_id": run_id}, headers={"Authorization": f"Bearer {key}"}, timeout=10.0)
        entries = resp.json()["entries"]
        if entries and all(e["anchorStatus"] == "confirmed" for e in entries):
            break
        time.sleep(1)
    assert entries and entries[0]["anchorStatus"] == "confirmed", "step was not anchored within 30s"

    _run(["--api-key", key, "verify", run_id])
    out = capsys.readouterr().out
    assert "VERIFIED" in out
    assert "All 1 step(s)" in out
    assert "FAILED" not in out


def test_verify_command_reports_pending_and_exits_nonzero(capsys):
    from trustchain_sdk import TrustChain

    key = _fresh_api_key(["runs:read", "runs:write", "logs:write"])
    tc = TrustChain(key, base_url=BASE_URL, on_error="raise")
    agent_id = f"cli_test_agent_{uuid.uuid4().hex[:8]}"
    run_id = tc._current_run_id(agent_id)
    receipt = tc.log_and_wait(agent_id=agent_id, action="answer", input="q", output="a")
    assert receipt.step_id is not None

    with pytest.raises(SystemExit) as exc_info:
        _run(["--api-key", key, "verify", run_id])
    assert exc_info.value.code != 0
    out = capsys.readouterr().out
    assert "PENDING" in out


def test_verify_command_unknown_run_exits_nonzero(capsys):
    key = _fresh_api_key(["runs:read"])
    with pytest.raises(SystemExit):
        _run(["--api-key", key, "verify", "run_does_not_exist"])


# ── integrity verify-content (Phase 4 G3) ─────────────────────────────────
# Closes a real gap: the endpoint itself (backend/tests/test_verify_content.py)
# and the raw HTTP call (scripts/e2e_demo.py) were both proven, but the
# CLI-reachable form (cmd_integrity_verify_content) had never been
# exercised by anything before these three tests — same gap both SDKs had.

def _logged_step(agent_id_prefix: str, output: str) -> int:
    """Real content, logged via the SDK (not the CLI itself — verifying
    the CLI command's own behavior doesn't require the step to have been
    CREATED via the CLI too)."""
    from trustchain_sdk import TrustChain

    key = _fresh_api_key(["runs:read", "runs:write", "logs:write"])
    tc = TrustChain(key, base_url=BASE_URL, on_error="raise")
    agent_id = f"{agent_id_prefix}_{uuid.uuid4().hex[:8]}"
    receipt = tc.log_and_wait(agent_id=agent_id, action="answer_query", input="what is my refund", output=output)
    assert receipt.step_id is not None
    return key, receipt.step_id


def test_verify_content_command_matches_current_hash_with_no_tampering(capsys, monkeypatch):
    import io

    key, step_id = _logged_step("cli_vc_agent", "the refund is $50")
    monkeypatch.setattr("sys.stdin", io.StringIO("the refund is $50"))

    _run(["--api-key", key, "integrity", "verify-content", str(step_id), "output"])
    result = json.loads(capsys.readouterr().out)
    assert result["matchesCurrent"] is True
    assert result["matchesOriginal"] is None  # nothing has ever been changed


def test_verify_content_command_wrong_candidate_matches_neither(capsys, monkeypatch):
    import io

    key, step_id = _logged_step("cli_vc_agent", "the refund is $50")
    monkeypatch.setattr("sys.stdin", io.StringIO("something else entirely"))

    _run(["--api-key", key, "integrity", "verify-content", str(step_id), "output"])
    result = json.loads(capsys.readouterr().out)
    assert result["matchesCurrent"] is False


def test_verify_content_command_after_tampering_matches_original_not_current(capsys, monkeypatch):
    """The scenario this command exists for: after a step_row_tampered
    alert, the owner supplies the text their OWN systems recorded and
    confirms it against what the hash was before the edit — without
    TrustChain ever having stored that text itself (see docs/adr/0020)."""
    import io

    key, step_id = _logged_step("cli_vc_agent", "the refund is $50")
    _tamper_step_output_hash(step_id)

    monkeypatch.setattr("sys.stdin", io.StringIO("the refund is $50"))
    _run(["--api-key", key, "integrity", "verify-content", str(step_id), "output"])
    true_original = json.loads(capsys.readouterr().out)
    assert true_original["matchesCurrent"] is False   # the row was tampered — no longer matches
    assert true_original["matchesOriginal"] is True   # but DID match what the hash was before the edit

    monkeypatch.setattr("sys.stdin", io.StringIO("the refund is $5000"))
    _run(["--api-key", key, "integrity", "verify-content", str(step_id), "output"])
    wrong_guess = json.loads(capsys.readouterr().out)
    assert wrong_guess["matchesOriginal"] is False


# ── integrity verify-run ────────────────────────────────────────────────
# `trustchain integrity verify-run` (cmd_integrity_verify_run) had no
# dedicated test either — noticed while writing the verify-content tests
# above, not acted on until now. Synchronous per-run check (distinct from
# `verify`, which reads the already-materialized /audit-log view) — see
# POST /integrity/verify-run/{run_id}'s own docstring.

def test_verify_run_command_reports_all_verified_for_a_clean_run(capsys):
    from trustchain_sdk import TrustChain

    key = _fresh_api_key(["runs:read", "runs:write", "logs:write"])
    tc = TrustChain(key, base_url=BASE_URL, on_error="raise")
    agent_id = f"cli_vr_agent_{uuid.uuid4().hex[:8]}"
    run_id = tc._current_run_id(agent_id)
    receipt = tc.log_and_wait(agent_id=agent_id, action="answer", input="q", output="a")
    assert receipt.step_id is not None

    _run(["--api-key", key, "integrity", "verify-run", run_id])
    result = json.loads(capsys.readouterr().out)
    assert result["runId"] == run_id
    assert result["allVerified"] is True
    assert len(result["steps"]) == 1
    assert result["steps"][0]["verified"] is True
    assert result["steps"][0]["reason"] is None


def test_verify_run_command_unknown_run_exits_nonzero():
    key = _fresh_api_key(["runs:read"])
    with pytest.raises(SystemExit):
        _run(["--api-key", key, "integrity", "verify-run", "run_does_not_exist"])


# ── dev ───────────────────────────────────────────────────────────────────

def test_dev_status_finds_compose_file_and_runs(monkeypatch, capsys):
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    with pytest.raises(SystemExit) as exc_info:
        _run(["dev", "status"])
    assert exc_info.value.code == 0
