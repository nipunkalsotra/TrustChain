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
    # an empty task with the identical 400, same as
    # test_empty_task_raises_bad_request_error in the SDK's own suite).
    user = seed_user_and_token()
    r = client.post("/v1/run-agent", json={"task": ""}, headers=_auth_headers(user["token"]))
    assert r.status_code == 400


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

    v = client.get(f"/agents/{agent_id}/verify", params={"code_hash": code_hash}, headers=headers)
    assert v.status_code == 200, v.text
    result = v.json()
    assert result["isValid"] is True
    assert result["isActive"] is True
    assert result["hashMatches"] is True
    assert result["storedHash"] == code_hash


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
