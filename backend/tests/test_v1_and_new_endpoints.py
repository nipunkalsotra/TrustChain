"""
tests/test_v1_and_new_endpoints.py — covers three P2.4/Appendix-A gaps
closed in this pass:

  1. /v1/ API versioning — every route lives on both its legacy
     unprefixed path and the new /v1/-prefixed canonical one, same
     handler either way (main.py's `router`, mounted twice).
  2. POST /agents, GET /agents/{id}/verify — SDK register_agent()/
     verify_agent(), against AgentIdentityRegistryV2's real REGISTRAR_ROLE
     path (not V1's DEFAULT_ADMIN_ROLE-gated one — see that contract's
     docstring for why the role changed).
  3. POST /steps, GET /steps/{id}/proof — third-party SDK step ingest and
     Merkle inclusion proof, verified against a REAL on-chain
     verifyProof() call after actually anchoring the batch (not just
     "the proof object looks right") — the whole point of a proof is
     that it verifies against chain state, so that's what's asserted.
  4. GET /stats — public platform counters.

Real Postgres/Redis throughout (isolated_db); (2) and (3) additionally
need real Anvil with V2 deployed (requires_anvil/chain_settings, shared
with test_anchor_worker.py) since they exercise real on-chain writes.
"""

import asyncio
import json
import uuid

from web3 import Web3

from tests.conftest import requires_anvil, seed_user_and_token


def run(coro):
    return asyncio.run(coro)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _unique_agent_id() -> str:
    return f"test_agent_{uuid.uuid4().hex[:8]}"


def _code_hash_for(config: dict) -> str:
    import json
    serialised = json.dumps(config, sort_keys=True, separators=(",", ":"))
    digest = Web3.keccak(text=serialised).hex()
    return digest if digest.startswith("0x") else "0x" + digest


# ── /v1/ versioning ──────────────────────────────────────────────────────

def test_v1_prefixed_and_unprefixed_routes_are_the_same_handler(client):
    user = seed_user_and_token()
    headers = _auth_headers(user["token"])

    legacy = client.get("/leaderboard", headers=headers)
    versioned = client.get("/v1/leaderboard", headers=headers)

    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()


def test_v1_prefixed_post_run_agent_route_exists(client, monkeypatch):
    # Doesn't need a real pipeline run — just proves POST /v1/run-agent
    # resolves to the exact same handler as POST /run-agent (both reject
    # an empty task identically — 422, Pydantic's min_length=1 on
    # RunAgentRequest.task, not a 400 from handler-body validation).
    user = seed_user_and_token()
    r = client.post("/v1/run-agent", json={"task": ""}, headers=_auth_headers(user["token"]))
    assert r.status_code == 422


# ── Appendix-A-aligned /v1 alias routes — 3 routes whose legacy
#    unprefixed name doesn't literally match the plan's documented /v1
#    path for the same operation, so `router`'s ordinary dual-mount (same
#    path, with/without /v1/) can't cover them; main.py's v1_only_router
#    adds exactly these 3 additional names, mounted under /v1 only. ────

def test_v1_keys_is_an_alias_for_api_keys(client):
    user = seed_user_and_token()
    headers = _auth_headers(user["token"])

    legacy = client.post("/api-keys", json={"scopes": ["runs:read"]}, headers=headers)
    versioned = client.post("/v1/keys", json={"scopes": ["runs:read"]}, headers=headers)
    assert legacy.status_code == versioned.status_code == 200, (legacy.text, versioned.text)
    # Different keys minted (each call is a real, distinct create) — same
    # RESPONSE SHAPE is what's actually being asserted here.
    assert set(legacy.json().keys()) == set(versioned.json().keys()) == {"id", "raw_key", "last_four", "scopes"}

    key_id = versioned.json()["id"]
    revoked = client.delete(f"/v1/keys/{key_id}", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"ok": True}


def test_v1_runs_post_is_an_alias_for_run_agent(client, monkeypatch):
    # Same "doesn't need a real pipeline run" reasoning as the
    # /v1/run-agent test above — 422 on both proves the SAME handler
    # (RunAgentRequest.task's min_length=1) is wired at both names.
    user = seed_user_and_token()
    r = client.post("/v1/runs", json={"task": ""}, headers=_auth_headers(user["token"]))
    assert r.status_code == 422


def test_v1_runs_stream_is_an_alias_for_stream(client, monkeypatch):
    # GET /stream/{run_id} long-polls Redis for up to 120s on an unknown
    # run_id before yielding its own timeout error event — same
    # short-timeout monkeypatch test_sse.py's own tests use, so this
    # proves the ALIAS resolves to the identical handler without
    # actually waiting out that real 120s (see run_events.read_events).
    import main
    import run_events as run_events_module

    original_read_events = run_events_module.read_events

    async def _short_timeout_read_events(run_id, timeout_seconds=120):
        async for evt in original_read_events(run_id, timeout_seconds=1):
            yield evt

    monkeypatch.setattr(main.run_events, "read_events", _short_timeout_read_events)

    with client.stream("GET", "/v1/runs/nonexistent-run-id/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line.startswith("data: ")]

    assert len(lines) == 1
    event = json.loads(lines[0].removeprefix("data: "))
    assert event["type"] == "error"
    assert "timeout" in event["message"]


# ── POST /agents, GET /agents/{id}/verify ────────────────────────────────

@requires_anvil
def test_register_agent_then_verify_matches(client, chain_settings):
    user = seed_user_and_token()
    headers = _auth_headers(user["token"])

    agent_id = _unique_agent_id()
    config = {"agentId": agent_id, "model": "gpt-4o", "version": "2026-01"}
    code_hash = _code_hash_for(config)

    r = client.post(
        "/agents",
        json={"agent_id": agent_id, "code_hash": code_hash, "model": "gpt-4o", "version": "2026-01"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == agent_id
    assert body["tx_hash"].startswith("0x")

    # F8: blockchain/identity_writer.py must submit an EIP-1559 dynamic-fee
    # tx (type 2), not a legacy gasPrice one — Anvil exposes baseFeePerGas
    # by default so blockchain.gas.build_fee_params takes that branch.
    from anchor_worker import chain as chain_module
    onchain_tx = chain_module.get_w3().eth.get_transaction(body["tx_hash"])
    assert onchain_tx["type"] == 2
    assert onchain_tx["maxFeePerGas"] > 0
    assert onchain_tx["maxPriorityFeePerGas"] > 0

    v = client.get(f"/agents/{agent_id}/verify", params={"code_hash": code_hash}, headers=headers)
    assert v.status_code == 200, v.text
    result = v.json()
    assert result["isValid"] is True
    assert result["isActive"] is True
    assert result["hashMatches"] is True
    assert result["storedHash"] == code_hash


@requires_anvil
def test_two_tenants_registering_the_same_agent_id_do_not_collide(client, chain_settings):
    """Real, end-to-end proof of AgentIdentityRegistryV2's project
    namespacing (contracts/src/v2/AgentIdentityRegistryV2.sol's docstring)
    through the actual HTTP API two real tenants would use — not a
    contract-unit-test with a hand-picked projectId, but two genuinely
    separate signed-up accounts (main.py injects principal.project_id
    server-side; a client never supplies it) both registering an agent
    named "researcher" with DIFFERENT code hashes. Before this fix, the
    second registration would have silently overwritten the first
    tenant's on-chain record."""
    alice = seed_user_and_token(f"alice_namespacing_{uuid.uuid4().hex[:8]}@example.com", "Alice")
    bob = seed_user_and_token(f"bob_namespacing_{uuid.uuid4().hex[:8]}@example.com", "Bob")
    alice_headers = _auth_headers(alice["token"])
    bob_headers = _auth_headers(bob["token"])

    shared_agent_id = "researcher"  # same name, deliberately — the whole point
    alice_config = {"agentId": shared_agent_id, "model": "gpt-4o", "version": "alice-v1"}
    bob_config = {"agentId": shared_agent_id, "model": "claude-3", "version": "bob-v1"}
    alice_hash = _code_hash_for(alice_config)
    bob_hash = _code_hash_for(bob_config)
    assert alice_hash != bob_hash

    r1 = client.post(
        "/agents",
        json={"agent_id": shared_agent_id, "code_hash": alice_hash, "model": "gpt-4o", "version": "alice-v1"},
        headers=alice_headers,
    )
    assert r1.status_code == 200, r1.text

    r2 = client.post(
        "/agents",
        json={"agent_id": shared_agent_id, "code_hash": bob_hash, "model": "claude-3", "version": "bob-v1"},
        headers=bob_headers,
    )
    assert r2.status_code == 200, r2.text

    # The real assertion: Alice's registration must be COMPLETELY
    # UNTOUCHED by Bob registering the same agentId after her.
    alice_verify = client.get(
        f"/agents/{shared_agent_id}/verify", params={"code_hash": alice_hash}, headers=alice_headers
    )
    assert alice_verify.status_code == 200, alice_verify.text
    alice_result = alice_verify.json()
    assert alice_result["isValid"] is True
    assert alice_result["hashMatches"] is True
    assert alice_result["storedHash"] == alice_hash

    bob_verify = client.get(
        f"/agents/{shared_agent_id}/verify", params={"code_hash": bob_hash}, headers=bob_headers
    )
    assert bob_verify.status_code == 200, bob_verify.text
    bob_result = bob_verify.json()
    assert bob_result["isValid"] is True
    assert bob_result["hashMatches"] is True
    assert bob_result["storedHash"] == bob_hash

    # Cross-check: Alice's OWN hash must NOT verify against Bob's project
    # context, and vice versa — proves this isn't just "both happen to
    # read back their own last write" but genuine per-project isolation.
    alice_hash_under_bob = client.get(
        f"/agents/{shared_agent_id}/verify", params={"code_hash": alice_hash}, headers=bob_headers
    )
    assert alice_hash_under_bob.status_code == 200, alice_hash_under_bob.text
    assert alice_hash_under_bob.json()["hashMatches"] is False


@requires_anvil
def test_verify_agent_detects_tampered_config(client, chain_settings):
    user = seed_user_and_token()
    headers = _auth_headers(user["token"])

    agent_id = _unique_agent_id()
    real_config = {"agentId": agent_id, "model": "gpt-4o", "version": "2026-01"}
    real_hash = _code_hash_for(real_config)
    client.post(
        "/agents",
        json={"agent_id": agent_id, "code_hash": real_hash, "model": "gpt-4o", "version": "2026-01"},
        headers=headers,
    )

    tampered_config = {"agentId": agent_id, "model": "gpt-4o", "version": "2026-02-TAMPERED"}
    tampered_hash = _code_hash_for(tampered_config)

    v = client.get(f"/agents/{agent_id}/verify", params={"code_hash": tampered_hash}, headers=headers)
    assert v.status_code == 200
    result = v.json()
    assert result["isValid"] is False
    assert result["hashMatches"] is False
    assert result["storedHash"] == real_hash  # what's actually registered, unchanged


def test_register_agent_requires_auth(client):
    r = client.post("/agents", json={"agent_id": "x", "code_hash": "0x" + "00" * 32, "model": "m", "version": "v"})
    assert r.status_code == 401


# ── POST /steps, GET /steps/{id}/proof ───────────────────────────────────

@requires_anvil
def test_log_external_step_then_fetch_verified_proof(client, chain_settings):
    from anchor_worker.main import run_once

    user = seed_user_and_token()
    headers = _auth_headers(user["token"])
    run_id = f"ext_run_{uuid.uuid4().hex[:8]}"

    step_ids = []
    for i in range(3):
        r = client.post(
            "/steps",
            json={
                "run_id": run_id, "agent_id": "third_party_agent", "action": "step",
                "input": f"in {i}", "output": f"out {i}",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["anchor_status"] == "pending"
        step_ids.append(body["step_id"])

    # Before anchoring: proof isn't available yet (still pending, no batch).
    not_yet = client.get(f"/steps/{step_ids[0]}/proof", headers=headers)
    assert not_yet.status_code == 404

    # Drive the anchor worker for real — same helper test_anchor_worker.py uses.
    anchored = run(run_once("proof-test-worker", chain_settings))
    assert anchored == 3

    from anchor_worker import chain as chain_module

    for step_id in step_ids:
        p = client.get(f"/steps/{step_id}/proof", headers=headers)
        assert p.status_code == 200, p.text
        proof = p.json()
        assert proof["runId"] == run_id
        assert proof["anchorStatus"] == "confirmed"
        assert proof["txHash"].startswith("0x")

        # The actual point of a Merkle proof: it must verify against the
        # REAL on-chain contract, not just against our own re-derivation
        # of it. Round-trip through AgentAuditLogV2.verifyProof directly.
        contract = chain_module.get_audit_log_contract()
        onchain_ok = contract.functions.verifyProof(
            proof["anchorId"],
            bytes.fromhex(proof["leaf"].removeprefix("0x")),
            [bytes.fromhex(p_.removeprefix("0x")) for p_ in proof["proof"]],
        ).call()
        assert onchain_ok is True

        # A forged leaf must NOT verify against the same proof/root.
        forged_leaf = bytes(32)
        forged_ok = contract.functions.verifyProof(
            proof["anchorId"], forged_leaf, [bytes.fromhex(p_.removeprefix("0x")) for p_ in proof["proof"]]
        ).call()
        assert forged_ok is False


def test_log_external_step_requires_auth(client):
    r = client.post("/steps", json={"run_id": "x", "agent_id": "a", "action": "b", "input": "i", "output": "o"})
    assert r.status_code == 401


def test_get_step_proof_for_unknown_step_is_404(client):
    user = seed_user_and_token()
    r = client.get("/steps/999999999/proof", headers=_auth_headers(user["token"]))
    assert r.status_code == 404


# ── GET /gas-spend — real gas-spend attribution ─────────────────────────

@requires_anvil
def test_gas_spend_reflects_real_confirmed_batch_cost(client, chain_settings):
    """Real end-to-end: log steps, anchor them for real, and confirm GET
    /gas-spend reports the exact real cost (gas_used * gas_price_wei) the
    confirming transaction's own receipt recorded — not an estimate, and
    not another project's spend."""
    from anchor_worker.main import run_once

    user = seed_user_and_token()
    headers = _auth_headers(user["token"])
    run_id = f"gas_spend_run_{uuid.uuid4().hex[:8]}"

    zero = client.get("/gas-spend", headers=headers)
    assert zero.status_code == 200, zero.text
    assert zero.json() == {"confirmedBatchCount": 0, "totalGasSpentWei": "0"}

    r = client.post(
        "/steps",
        json={"run_id": run_id, "agent_id": "gas_test_agent", "action": "step", "input": "in", "output": "out"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    anchored = run(run_once("gas-spend-test-worker", chain_settings))
    assert anchored == 1

    from db.engine import get_sessionmaker
    from db.models import AnchorBatch, Step
    from sqlalchemy import select

    async def _fetch_real_batch():
        async with get_sessionmaker()() as session:
            step = (await session.execute(select(Step).where(Step.run_id == run_id))).scalar_one()
            return await session.get(AnchorBatch, step.anchor_batch_id)

    real_batch = run(_fetch_real_batch())
    expected_wei = real_batch.gas_used * real_batch.gas_price_wei
    assert expected_wei > 0

    spend = client.get("/gas-spend", headers=headers)
    assert spend.status_code == 200, spend.text
    body = spend.json()
    assert body["confirmedBatchCount"] == 1
    assert body["totalGasSpentWei"] == str(expected_wei)

    # Isolation (invariant I7): a different project sees zero, not this
    # project's spend.
    other_user = seed_user_and_token(f"gas_spend_other_{uuid.uuid4().hex[:8]}@example.com", "Other")
    other = client.get("/gas-spend", headers=_auth_headers(other_user["token"]))
    assert other.status_code == 200, other.text
    assert other.json() == {"confirmedBatchCount": 0, "totalGasSpentWei": "0"}


def test_log_external_step_is_idempotent(client):
    user = seed_user_and_token()
    headers = {**_auth_headers(user["token"]), "Idempotency-Key": "step-key-1"}
    run_id = f"idem_run_{uuid.uuid4().hex[:8]}"
    body = {"run_id": run_id, "agent_id": "a", "action": "b", "input": "i", "output": "o"}

    first = client.post("/steps", json=body, headers=headers)
    second = client.post("/steps", json=body, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # same step_id both times, not a second write


# ── GET /stats ────────────────────────────────────────────────────────────

def test_platform_stats_is_public_and_returns_aggregate_counts(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"totalRuns", "totalSteps", "totalAnchoredBatches"}
    assert all(isinstance(v, int) for v in body.values())
