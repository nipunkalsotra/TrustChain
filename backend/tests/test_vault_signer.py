"""
tests/test_vault_signer.py — verifies VaultKvSigner against a FAKE hvac
client (same testability hook AwsKmsSigner/GcpKmsSigner use — see
test_kms_signer.py) shaped like hvac's real
`client.secrets.kv.v2.read_secret_version(...)` response.

Unlike the KMS signers (which never see the raw key — only a digest goes
in, a signature comes out), VaultKvSigner fetches the raw key once and
delegates to LocalKeySigner from then on (see its docstring for why that's
an honest, disclosed limitation vs the KMS backends). So the check here is
simpler: the fetched key produces byte-identical signed transactions to
constructing LocalKeySigner directly with that same key.
"""

import pytest
from web3 import Web3

from blockchain.signer import LocalKeySigner, VaultKvSigner

TEST_KEY_HEX = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
TEST_KEY = "0x" + TEST_KEY_HEX


def _w3() -> Web3:
    return Web3()


class _FakeKvV2:
    def __init__(self, secrets: dict):
        self._secrets = secrets
        self.last_call: dict = {}

    def read_secret_version(self, path, mount_point):
        self.last_call = {"path": path, "mount_point": mount_point}
        return {"data": {"data": self._secrets[path]}}


class _FakeSecrets:
    def __init__(self, kv_v2: _FakeKvV2):
        self.kv = type("_Kv", (), {"v2": kv_v2})()


class FakeVaultClient:
    """Stands in for hvac.Client. Holds the private key only so the fake
    can hand it back on read — VaultKvSigner itself never sees anything
    beyond calling read_secret_version(), exactly as it would against a
    real Vault server."""

    def __init__(self, secrets_by_path: dict, authenticated: bool = True):
        self.secrets = _FakeSecrets(_FakeKvV2(secrets_by_path))
        self._authenticated = authenticated

    def is_authenticated(self) -> bool:
        return self._authenticated


DYNAMIC_FEE_TX = {
    "type": 2,
    "chainId": 1337,
    "nonce": 34,
    "to": "0x09616C3d61b3331fc4109a9E41a8BDB7d9776609",
    "value": 10**15,
    "gas": 100000,
    "maxFeePerGas": 2_000_000_000,
    "maxPriorityFeePerGas": 2_000_000_000,
    "data": "0x616263646566",
}


def test_vault_kv_signer_matches_local_key_signer():
    w3 = _w3()
    local = LocalKeySigner(TEST_KEY, w3)
    client = FakeVaultClient({"anchor-worker/key": {"private_key": TEST_KEY}})

    vault_signer = VaultKvSigner(
        "https://vault.example.internal", "fake-token", "anchor-worker/key", w3, client=client,
    )

    assert vault_signer.address == local.address

    local_signed = local.sign_transaction(dict(DYNAMIC_FEE_TX))
    vault_signed = vault_signer.sign_transaction(dict(DYNAMIC_FEE_TX))
    assert vault_signed.raw_transaction == local_signed.raw_transaction
    assert (vault_signed.v, vault_signed.r, vault_signed.s) == (local_signed.v, local_signed.r, local_signed.s)


def test_vault_kv_signer_uses_custom_mount_point_and_key_field():
    w3 = _w3()
    client = FakeVaultClient({"custom/path": {"eth_private_key": TEST_KEY}})

    vault_signer = VaultKvSigner(
        "https://vault.example.internal", "fake-token", "custom/path", w3, client=client,
        mount_point="kv-custom", key_field="eth_private_key",
    )

    assert vault_signer.address == LocalKeySigner(TEST_KEY, w3).address
    assert client.secrets.kv.v2.last_call == {"path": "custom/path", "mount_point": "kv-custom"}


def test_vault_kv_signer_requires_hvac_when_no_client_given():
    w3 = _w3()
    # hvac isn't a base dependency (see signer.py's module docstring) —
    # constructing without an injected client should either succeed (if
    # hvac happens to be installed) or fail with a clear ImportError.
    try:
        import hvac  # noqa: F401
        pytest.skip("hvac is installed in this environment — nothing to assert here")
    except ImportError:
        pass
    try:
        VaultKvSigner("https://vault.example.internal", "fake-token", "anchor-worker/key", w3)
        assert False, "expected ImportError when hvac is not installed"
    except ImportError as e:
        assert "hvac" in str(e)
