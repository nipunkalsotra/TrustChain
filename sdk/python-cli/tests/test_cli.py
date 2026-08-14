"""tests/test_cli.py — integration tests for the `trustchain` CLI, against
a REAL running API + real Anvil with V2 deployed — no mocking.

Run:
    docker compose up -d postgres redis anvil api anchor-worker indexer mcp-search mcp-blockchain
    pip install -e "../python" -e ".[dev]"
    pytest tests/test_cli.py -v
"""

import time
import uuid

import httpx
import pytest

from trustchain_cli import credentials
from trustchain_cli.main import build_parser

BASE_URL = "http://localhost:8000"
ANVIL_RPC = "http://localhost:8545"


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
    return email, password


def _fresh_api_key(scopes: list[str]) -> str:
    email = f"cli_test_{uuid.uuid4().hex}@example.com"
    signup = httpx.post(
        f"{BASE_URL}/auth/signup",
        json={"name": "cli test", "email": email, "password": "cli-test-password-123"},
        timeout=10.0,
    )
    assert signup.status_code == 200, signup.text
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


# ── dev ───────────────────────────────────────────────────────────────────

def test_dev_status_finds_compose_file_and_runs(monkeypatch, capsys):
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    with pytest.raises(SystemExit) as exc_info:
        _run(["dev", "status"])
    assert exc_info.value.code == 0
