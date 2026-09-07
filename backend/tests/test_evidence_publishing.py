"""
tests/test_evidence_publishing.py — evidence/ (manifest + pluggable
publisher). Follows test_email_delivery.py's exact pattern for the Pinata
adapter: a REAL local HTTP server on a real loopback socket standing in
for api.pinata.cloud, not a mock of httpx — only the URL differs from the
real one.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

_PINATA_PORT = 8827  # own port, same reasoning as test_email_delivery.py's fixed SMTP/Brevo ports


def run(coro):
    return asyncio.run(coro)


def _sample_batch(**overrides) -> dict:
    batch = {
        "batch_id": 1,
        "run_id": "run_evidence_test",
        "run_id_hash": "0x" + "ab" * 32,
        "root_hex": "0x" + "cd" * 32,
        "step_count": 3,
        "leaf_order": [101, 102, 103],
        "leaf_hashes": ["0x" + "11" * 32, "0x" + "22" * 32, "0x" + "33" * 32],
        "outbox_ids": [201, 202, 203],
    }
    batch.update(overrides)
    return batch


# ── Manifest: determinism + no-PII-by-construction ──────────────────────────

def test_manifest_is_deterministic_for_identical_batch_input():
    from evidence.manifest import build_manifest, serialize_manifest

    batch = _sample_batch()
    m1 = build_manifest(batch)
    m2 = build_manifest(_sample_batch())  # a SEPARATE dict, same values

    assert m1 == m2
    assert serialize_manifest(m1) == serialize_manifest(m2)


def test_manifest_changes_if_any_leaf_hash_changes():
    """The flip side of determinism — proves the manifest isn't trivially
    constant (e.g. accidentally hashing only metadata and ignoring the
    actual leaves)."""
    from evidence.manifest import build_manifest, serialize_manifest

    base = serialize_manifest(build_manifest(_sample_batch()))
    tampered = serialize_manifest(build_manifest(_sample_batch(leaf_hashes=["0x" + "11" * 32, "0x" + "99" * 32, "0x" + "33" * 32])))
    assert base != tampered


def test_manifest_contains_only_the_documented_whitelisted_fields():
    """No-PII policy, enforced structurally: build_manifest's signature
    only ever takes hash/count/id values (see _sample_batch — there is no
    field here that COULD hold a raw prompt, output, email, or token), so
    this asserts the OUTPUT never grows an extra field a future edit might
    accidentally add raw content through."""
    from evidence.manifest import build_manifest

    manifest = build_manifest(_sample_batch())
    assert set(manifest.keys()) == {"schemaVersion", "runIdHash", "merkleRoot", "stepCount", "leafOrder", "leafHashes"}
    # Every value is a hash/int/count — nothing free-text-shaped.
    assert isinstance(manifest["schemaVersion"], int)
    assert isinstance(manifest["stepCount"], int)
    assert all(isinstance(h, str) and h.startswith("0x") for h in manifest["leafHashes"])
    assert all(isinstance(i, int) for i in manifest["leafOrder"])


# ── Proof verification from manifest + chain data alone ─────────────────────

def test_proof_reconstructs_and_verifies_from_manifest_and_root_alone():
    """The actual point of publishing this manifest at all: prove a
    verifier can reconstruct and check ANY leaf's Merkle proof using
    NOTHING but the published manifest (leafHashes/leafOrder) plus the
    on-chain root — no TrustChain database access required. Restores
    ONLY those two things, deliberately not touching anchor_batches or
    steps at all past building the fixture."""
    from blockchain.merkle import build_tree, proof_for, verify_proof
    from evidence.manifest import build_manifest

    real_leaves = [bytes([i]) * 32 for i in range(5)]
    tree = build_tree(real_leaves)
    batch = _sample_batch(
        root_hex=tree.root_hex,
        step_count=5,
        leaf_order=[10, 11, 12, 13, 14],
        leaf_hashes=["0x" + leaf.hex() for leaf in real_leaves],
    )
    manifest = build_manifest(batch)

    # A verifier with ONLY this manifest + the on-chain root (never the
    # original `real_leaves` list, never Postgres) reconstructs the tree:
    reconstructed_leaves = [bytes.fromhex(h.removeprefix("0x")) for h in manifest["leafHashes"]]
    reconstructed_tree = build_tree(reconstructed_leaves)
    assert reconstructed_tree.root_hex == manifest["merkleRoot"]

    for i, leaf in enumerate(reconstructed_leaves):
        proof = proof_for(reconstructed_tree.levels, i)
        assert verify_proof(leaf, proof, reconstructed_tree.root)


# ── Disabled publisher (default) never fabricates a URI ─────────────────────

def test_disabled_publisher_always_raises_never_returns_a_uri():
    from evidence.backends.base import EvidencePublishError, get_publisher

    publisher = get_publisher("disabled")
    with pytest.raises(EvidencePublishError):
        run(publisher.publish(build_manifest_for_test()))


def build_manifest_for_test():
    from evidence.manifest import build_manifest
    return build_manifest(_sample_batch())


def test_unknown_backend_name_raises_immediately():
    from evidence.backends.base import get_publisher

    with pytest.raises(ValueError):
        get_publisher("not-a-real-backend")


# ── Pinata adapter against a real local HTTP server ──────────────────────────

class _CapturingPinataHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        body["_auth_header"] = self.headers.get("Authorization", "")
        body["_path"] = self.path
        type(self).received.append(body)
        response = json.dumps({"IpfsHash": "QmRealLocalTestCid123", "PinSize": 123, "Timestamp": "2026-01-01T00:00:00Z"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def real_pinata_server(monkeypatch):
    _CapturingPinataHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", _PINATA_PORT), _CapturingPinataHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("EVIDENCE_PUBLISHER_BACKEND", "pinata")
    monkeypatch.setenv("PINATA_JWT", "test-jwt-not-a-real-secret")
    monkeypatch.setenv("PINATA_API_URL", f"http://127.0.0.1:{_PINATA_PORT}/pinning/pinJSONToIPFS")
    from config import get_settings
    get_settings.cache_clear()

    yield _CapturingPinataHandler

    server.shutdown()
    server.server_close()
    get_settings.cache_clear()


def test_pinata_backend_publishes_to_a_real_server_and_returns_the_cid(real_pinata_server):
    from evidence.backends.pinata import PinataPublisher
    from evidence.manifest import build_manifest

    manifest = build_manifest(_sample_batch())
    result = run(PinataPublisher().publish(manifest))

    assert result.content_uri == "ipfs://QmRealLocalTestCid123"
    assert len(real_pinata_server.received) == 1
    received = real_pinata_server.received[0]
    assert received["_path"] == "/pinning/pinJSONToIPFS"
    assert received["_auth_header"] == "Bearer test-jwt-not-a-real-secret"
    # The manifest sent over the wire is EXACTLY the no-PII manifest —
    # nothing added, nothing substituted.
    assert received["pinataContent"] == manifest


def test_pinata_backend_without_jwt_raises_without_making_a_network_call(monkeypatch):
    from config import get_settings
    monkeypatch.setenv("EVIDENCE_PUBLISHER_BACKEND", "pinata")
    monkeypatch.setenv("PINATA_JWT", "")
    get_settings.cache_clear()

    from evidence.backends.base import EvidencePublishError
    from evidence.backends.pinata import PinataPublisher

    with pytest.raises(EvidencePublishError, match="pinata_jwt is not configured"):
        run(PinataPublisher().publish(build_manifest_for_test()))
    get_settings.cache_clear()


def test_pinata_backend_surfaces_a_real_http_error(monkeypatch):
    """A server that rejects the request (bad JWT, suspended account,
    plan limit) — the error body must reach anchor_batches' logs
    verbatim, not get swallowed into a generic failure."""
    class _RejectingHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({"error": {"reason": "INVALID_CREDENTIALS", "details": "bad jwt"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    port = _PINATA_PORT + 1
    server = ThreadingHTTPServer(("127.0.0.1", port), _RejectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("EVIDENCE_PUBLISHER_BACKEND", "pinata")
        monkeypatch.setenv("PINATA_JWT", "test-jwt")
        monkeypatch.setenv("PINATA_API_URL", f"http://127.0.0.1:{port}/pinning/pinJSONToIPFS")
        from config import get_settings
        get_settings.cache_clear()

        from evidence.backends.base import EvidencePublishError
        from evidence.backends.pinata import PinataPublisher

        with pytest.raises(EvidencePublishError, match="INVALID_CREDENTIALS"):
            run(PinataPublisher().publish(build_manifest_for_test()))
    finally:
        server.shutdown()
        server.server_close()
        get_settings.cache_clear()


# ── Anchor worker wiring: CID persistence, disabled-by-default, retry-safe ──

def test_publish_evidence_persists_cid_on_the_batch_row(client, real_pinata_server):
    """End-to-end through anchor_worker.main.publish_evidence against a
    real Postgres (the `client` fixture's app lifespan already stood one
    up — see conftest.py) and the real local Pinata stand-in above.
    anchor_batches carries no FK to runs/users (just a run_id_hash
    string), so this seeds the batch row directly rather than going
    through a full signup + run-agent flow."""
    import anchor_worker.main as anchor_worker_main
    from config import get_settings
    from db.engine import get_sessionmaker
    from db.models import AnchorBatch
    from sqlalchemy import text as sa_text

    session_factory = get_sessionmaker()

    async def _seed_and_publish():
        async with session_factory() as session:
            await session.execute(sa_text(
                "INSERT INTO anchor_batches (run_id_hash, merkle_root, step_count, leaf_order, status, created_at) "
                "VALUES (:h, :r, 1, '[1]', 'building', 0) RETURNING id"
            ), {"h": "0x" + "ab" * 32, "r": "0x" + "cd" * 32})
            batch_id = (await session.execute(sa_text("SELECT id FROM anchor_batches WHERE run_id_hash = :h ORDER BY id DESC LIMIT 1"), {"h": "0x" + "ab" * 32})).scalar_one()
            await session.commit()

        batch = _sample_batch(batch_id=batch_id)
        settings = get_settings()
        async with session_factory() as session:
            meta_uri = await anchor_worker_main.publish_evidence(session, batch, settings)

        async with session_factory() as session:
            row = await session.get(AnchorBatch, batch_id)
            return meta_uri, row.evidence_cid

    meta_uri, persisted_cid = run(_seed_and_publish())
    assert meta_uri == "ipfs://QmRealLocalTestCid123"
    assert persisted_cid == "ipfs://QmRealLocalTestCid123"


def test_publish_evidence_returns_empty_string_when_disabled(client):
    """The default (evidence_publisher_backend=disabled) — proves
    anchoring itself is never blocked or altered by evidence publishing
    being off, which is what every deployment gets until explicitly
    configured otherwise."""
    import anchor_worker.main as anchor_worker_main
    from config import get_settings
    from db.engine import get_sessionmaker

    get_settings.cache_clear()
    settings = get_settings()
    assert settings.evidence_publisher_backend == "disabled"

    session_factory = get_sessionmaker()

    async def _publish():
        async with session_factory() as session:
            return await anchor_worker_main.publish_evidence(session, _sample_batch(batch_id=999999), settings)

    meta_uri = run(_publish())
    assert meta_uri == ""
