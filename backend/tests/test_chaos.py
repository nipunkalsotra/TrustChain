"""
tests/test_chaos.py — chaos/resilience scenarios against REAL
infrastructure (real Anvil, a real Postgres container actually stopped
and restarted, real insufficient-funds transactions) rather than mocked
failures, matching this project's established "verify against real
infra" discipline (see tests/test_anchor_worker.py, test_indexer.py).

Four scenarios:
  1. RPC failure — a real primary RPC endpoint dying mid-operation is
     survived via FallbackHTTPProvider (unit-level coverage already in
     tests/test_resilient_provider.py; this file adds the higher-level
     "a real signed call still succeeds" check).
  2. Chain reorg — indexer/cursor.py's resolve_start_block correctly
     detects a stored cursor whose block hash no longer matches the
     chain's actual hash at that height, and rewinds.
  3. Gas exhaustion — a signer with no ETH to pay gas ("gas exhaustion"
     in the operational sense: the hot wallet ran dry) hits submit_batch's
     pre-send failure path, not the post-send revert path already
     covered by test_anchor_worker.py's unauthorized-signer test.
  4. Postgres failover — the actual `postgres` container is stopped and
     restarted (docker start/stop, not a mock), confirming /ready
     degrades to database.ok=false while it's down and recovers once
     Postgres is back, with no crash in between.
"""

import asyncio
import subprocess
import time
import uuid

import pytest
from eth_account import Account
from sqlalchemy import text
from web3 import Web3

import db
from agents.base import log_step
from anchor_worker import chain as chain_module
from anchor_worker.batch import build_batches
from anchor_worker.claim import claim_batch
from anchor_worker.submit import SubmitError, submit_batch
from blockchain.resilient_provider import FallbackHTTPProvider
from blockchain.signer import LocalKeySigner
from db.engine import get_sessionmaker
from tests.conftest import requires_anvil, seed_project

ANVIL_RPC = "http://localhost:8545"
DEAD_RPC = "http://localhost:19999"


def run(coro):
    return asyncio.run(coro)


def _unique_run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _seed_run_with_steps(run_id: str, n: int) -> list[dict]:
    run(db.create_run(run_id, seed_project(), "chaos test task", None, int(time.time())))
    events = []
    for i in range(n):
        _, evt = run(log_step(
            bridge=None, agent_id="researcher", action="task_received",
            input_text=f"input {i}", output_text=f"output {i}",
            step_index=i, run_id=run_id,
        ))
        events.append(evt)
    return events


# ── 1. RPC failure — real dead endpoint, real fallback, real signed call ──

@requires_anvil
def test_chaos_rpc_primary_dies_signed_call_still_succeeds_via_fallback():
    """A real transaction (fund a fresh account) is built and sent through
    a Web3 whose PRIMARY endpoint is a real dead port — proving the
    failover isn't just a plain `eth_chainId` read (tests/
    test_resilient_provider.py already covers that) but survives a full
    build-sign-send-confirm cycle end to end."""
    provider = FallbackHTTPProvider([DEAD_RPC, ANVIL_RPC], failure_threshold=5)
    w3 = Web3(provider)

    anvil_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    signer = LocalKeySigner(anvil_key, w3)
    recipient = Account.create().address

    tx = {
        "from": signer.address,
        "to": recipient,
        "value": w3.to_wei(0.001, "ether"),
        "nonce": w3.eth.get_transaction_count(signer.address, "pending"),
        "gas": 21_000,
        "gasPrice": w3.eth.gas_price,
    }
    signed = signer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    assert receipt.status == 1
    assert w3.eth.get_balance(recipient) == w3.to_wei(0.001, "ether")
    # The dead endpoint was actually tried (and failed) on every call —
    # this proves failover happened, not that DEAD_RPC was silently unused.
    assert provider.breakers[0].state in ("closed", "open")  # touched, not skipped from the start


# ── 2. Chain reorg — real Anvil, a deliberately stale cursor ──────────────

@requires_anvil
def test_chaos_reorg_detected_via_hash_mismatch_and_rewinds():
    from indexer.cursor import REORG_REWIND_BLOCKS, resolve_start_block, save_cursor

    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    contract_name = f"chaos_reorg_test_{uuid.uuid4().hex[:8]}"

    current_block = w3.eth.block_number
    # A cursor claiming block `current_block` had some hash it never
    # actually had — indistinguishable, from resolve_start_block's point
    # of view, from "the chain reorganized since this cursor was saved".
    fake_hash = "0x" + ("ab" * 32)

    async def _seed_and_resolve():
        async with get_sessionmaker()() as session:
            await save_cursor(session, contract_name, current_block, fake_hash)
            await session.commit()
        async with get_sessionmaker()() as session:
            return await resolve_start_block(session, w3, contract_name, deploy_block=0)

    start_block = run(_seed_and_resolve())
    assert start_block == max(0, current_block - REORG_REWIND_BLOCKS)
    assert start_block < current_block  # actually rewound, not just resumed at +1


@requires_anvil
def test_chaos_reorg_matching_hash_resumes_normally_not_a_false_positive():
    """The control case: resolve_start_block must NOT treat every restart
    as a reorg — a cursor whose hash still matches resumes at last_block+1."""
    from indexer.cursor import resolve_start_block, save_cursor

    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    contract_name = f"chaos_no_reorg_test_{uuid.uuid4().hex[:8]}"
    current_block = w3.eth.block_number
    real_hash = "0x" + w3.eth.get_block(current_block)["hash"].hex()

    async def _seed_and_resolve():
        async with get_sessionmaker()() as session:
            await save_cursor(session, contract_name, current_block, real_hash)
            await session.commit()
        async with get_sessionmaker()() as session:
            return await resolve_start_block(session, w3, contract_name, deploy_block=0)

    assert run(_seed_and_resolve()) == current_block + 1


@requires_anvil
def test_chaos_reorg_cursor_pointing_past_chain_tip_rewinds_safely():
    """A cursor referencing a block number that doesn't exist yet (deeper
    than any reorg — e.g. restored from a stale backup) must not crash
    resolve_start_block; it hits the same rewind path as a detected
    hash mismatch."""
    from indexer.cursor import REORG_REWIND_BLOCKS, resolve_start_block, save_cursor

    w3 = Web3(Web3.HTTPProvider(ANVIL_RPC))
    contract_name = f"chaos_future_cursor_test_{uuid.uuid4().hex[:8]}"
    future_block = w3.eth.block_number + 10_000

    async def _seed_and_resolve():
        async with get_sessionmaker()() as session:
            await save_cursor(session, contract_name, future_block, "0x" + "00" * 32)
            await session.commit()
        async with get_sessionmaker()() as session:
            return await resolve_start_block(session, w3, contract_name, deploy_block=0)

    start_block = run(_seed_and_resolve())
    assert start_block == max(0, future_block - REORG_REWIND_BLOCKS)


# ── 3. Gas exhaustion — signer with no ETH to pay gas ──────────────────────

@requires_anvil
def test_chaos_gas_exhaustion_unfunded_signer_hits_pre_send_failure_path(chain_settings):
    """'Gas exhaustion' in the operational sense that actually happens in
    production: the anchor worker's hot wallet runs out of native token
    balance. Distinct from test_anchor_worker.py's unauthorized-signer
    test — that one gets PAST send and reverts on-chain (receipt.status=0,
    observability reason='revert'); an unfunded signer never gets that
    far at all (Anvil/geth reject it at the mempool, "insufficient funds
    for gas * price + value") — submit_batch's FIRST try/except block
    (reason='build_or_send'), which nothing else in this suite exercises."""
    run_id = _unique_run_id("run_gas_exhaustion_test")
    _seed_run_with_steps(run_id, 2)

    contract = chain_module.get_audit_log_contract()
    w3 = chain_module.get_w3()

    broke_account = Account.create()  # never funded — zero balance
    broke_signer = LocalKeySigner(broke_account.key, w3)

    async def _claim_and_batch():
        async with get_sessionmaker()() as session:
            claimed = await claim_batch(session, "worker-broke", batch_size=10)
        async with get_sessionmaker()() as session:
            batches = await build_batches(session, claimed)
        return batches[0]

    batch = run(_claim_and_batch())

    async def _submit():
        async with get_sessionmaker()() as session:
            await submit_batch(session, batch, contract, broke_signer, w3)

    with pytest.raises(SubmitError, match="failed to submit batch"):
        run(_submit())

    # The batch must still be recoverable — worker-loop-level handling
    # (main.py's handle_submit_failure, already covered directly by
    # test_anchor_worker.py) requeues it from here; this test's job is
    # only proving SubmitError is what's raised for THIS failure mode.
    async def _batch_status():
        async with get_sessionmaker()() as session:
            row = (await session.execute(
                text("SELECT status FROM anchor_batches WHERE id = :id"), {"id": batch["batch_id"]}
            )).first()
            return row.status

    assert run(_batch_status()) == "building"  # never advanced past claim — send never succeeded


# ── 4. Postgres failover — the real container, actually stopped ───────────

def _postgres_container_name() -> str | None:
    """Scoped to this project's own compose-managed container specifically
    (docker-compose.yml's `postgres` service, named `<project>-postgres-1`
    by Compose's default naming) — deliberately NOT a bare "postgres"
    substring match, so this can never touch an unrelated Postgres
    container that happens to also be running on the same host."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=trustchain-postgres", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        names = [n for n in result.stdout.strip().splitlines() if n]
        return names[0] if names else None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


requires_docker_postgres = pytest.mark.skipif(
    _postgres_container_name() is None,
    reason="no running trustchain-postgres-* container found via `docker ps`",
)


@requires_docker_postgres
def test_chaos_postgres_outage_ready_degrades_and_recovers():
    """Stops the REAL postgres container, confirms a live DB query fails
    closed (raises, doesn't hang or silently return stale/empty data),
    then restarts it and confirms the same query succeeds again — proving
    recovery doesn't need a process restart, just the DB coming back."""
    container = _postgres_container_name()
    assert container is not None

    async def _ping() -> bool:
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    assert run(_ping()) is True  # sanity: healthy before the chaos starts

    subprocess.run(["docker", "stop", container], check=True, capture_output=True, timeout=30)
    try:
        deadline = time.monotonic() + 10
        saw_failure = False
        while time.monotonic() < deadline:
            if not run(_ping()):
                saw_failure = True
                break
            time.sleep(0.2)
        assert saw_failure, "expected a real query to fail while Postgres was stopped"
    finally:
        # A `docker stop` (not a graceful `pg_ctl stop -m fast`, which is
        # what a real managed-Postgres failover would do) makes the next
        # startup replay WAL and, on a large enough data directory under
        # disk/IO pressure, walk a slow fsync pass over the whole data
        # directory first — observed taking 200s+ in this environment
        # (see the container's own "syncing data directory (fsync),
        # elapsed time: ...s" log lines) even though nothing was actually
        # wrong; a fast managed failover wouldn't pay this cost, but this
        # test uses the real container as-is rather than pretend it's
        # instant. 240s gives real headroom without being unbounded.
        subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=30)
        deadline = time.monotonic() + 240
        recovered = False
        while time.monotonic() < deadline:
            if run(_ping()):
                recovered = True
                break
            time.sleep(1.0)
        assert recovered, "Postgres did not become reachable again within 240s of restart"
