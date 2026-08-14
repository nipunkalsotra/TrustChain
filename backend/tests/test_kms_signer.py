"""
tests/test_kms_signer.py — verifies AwsKmsSigner / GcpKmsSigner against a
FAKE KMS client that wraps a locally-held key (so we can generate the DER
signature/public key a real KMS would return) but the signer classes under
test never see that raw key — they only ever call `.sign()` / `.get_
public_key()`, exactly as they would against real AWS/GCP.

The check: sign the SAME transaction (both an EIP-1559 dynamic-fee tx and
a legacy gasPrice tx, since the v-encoding rule differs between the two —
see signer.py's `_v_for_unsigned_tx`) with LocalKeySigner (known-correct,
eth_account's own code path) and with each KMS signer, and assert the
resulting raw transaction bytes are byte-identical. Any bug in DER
decoding, low-s normalization, recovery-id brute force, or v-encoding
would produce a *different* (or invalid) signature — a mismatch here, not
a "looks plausible" success, is what a real bug would look like.
"""

from cryptography.hazmat.primitives.asymmetric.ec import SECP256K1, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from eth_keys import keys
from web3 import Web3

from blockchain.signer import AwsKmsSigner, GcpKmsSigner, LocalKeySigner

TEST_KEY_HEX = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
TEST_KEY = "0x" + TEST_KEY_HEX


def _w3() -> Web3:
    # No RPC needed — LocalKeySigner/KMS signers only use w3 for the
    # eth_account/checksumming helpers hung off w3.eth, not for network
    # calls, so an unconnected provider is fine here.
    return Web3()


class FakeAwsKmsClient:
    """Stands in for boto3's KMS client. Holds the private key only to
    produce the same signature a real KMS-held key would — AwsKmsSigner
    itself never touches `_private_key`, only calls sign()/get_public_key()."""

    def __init__(self, private_key_hex: str):
        self._private_key = keys.PrivateKey(bytes.fromhex(private_key_hex))

    def get_public_key(self, KeyId):
        raw_point = self._private_key.public_key.to_bytes()  # 64 bytes, X||Y
        pubkey = EllipticCurvePublicKey.from_encoded_point(SECP256K1(), b"\x04" + raw_point)
        der = pubkey.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return {"PublicKey": der}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        assert MessageType == "DIGEST"
        assert SigningAlgorithm == "ECDSA_SHA_256"
        sig = self._private_key.sign_msg_hash(Message)
        der = encode_dss_signature(sig.r, sig.s)
        return {"Signature": der}


class FakeGcpPublicKeyResponse:
    def __init__(self, pem: str):
        self.pem = pem


class FakeGcpSignResponse:
    def __init__(self, signature: bytes):
        self.signature = signature


class FakeGcpKmsClient:
    """Stands in for google.cloud.kms.KeyManagementServiceClient. Same
    custody model as FakeAwsKmsClient — GcpKmsSigner never sees the key."""

    def __init__(self, private_key_hex: str):
        self._private_key = keys.PrivateKey(bytes.fromhex(private_key_hex))

    def get_public_key(self, request):
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw_point = self._private_key.public_key.to_bytes()
        pubkey = EllipticCurvePublicKey.from_encoded_point(SECP256K1(), b"\x04" + raw_point)
        pem = pubkey.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
        return FakeGcpPublicKeyResponse(pem)

    def asymmetric_sign(self, request):
        digest = request["digest"]["sha256"]
        sig = self._private_key.sign_msg_hash(digest)
        der = encode_dss_signature(sig.r, sig.s)
        return FakeGcpSignResponse(der)


# EIP-1559 dynamic-fee tx (type 2) — exercises the "raw 0/1 recovery id" v-encoding path.
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

# Legacy tx (no `type`) — exercises the EIP-155 "recovery_id + chainId*2 + 35" v-encoding path.
LEGACY_TX = {
    "to": "0xF0109fC8DF283027b6285cc889F5aA624EaC1F55",
    "value": 1_000_000_000,
    "gas": 2_000_000,
    "gasPrice": 234_567_897_654_321,
    "nonce": 0,
    "chainId": 1337,
}


def test_aws_kms_signer_matches_local_key_signer_for_dynamic_fee_tx():
    w3 = _w3()
    local = LocalKeySigner(TEST_KEY, w3)
    kms = AwsKmsSigner("fake-key-id", w3, client=FakeAwsKmsClient(TEST_KEY_HEX))

    assert kms.address == local.address

    local_signed = local.sign_transaction(dict(DYNAMIC_FEE_TX))
    kms_signed = kms.sign_transaction(dict(DYNAMIC_FEE_TX))

    assert kms_signed.raw_transaction == local_signed.raw_transaction
    assert kms_signed.hash == local_signed.hash
    assert (kms_signed.v, kms_signed.r, kms_signed.s) == (local_signed.v, local_signed.r, local_signed.s)


def test_aws_kms_signer_matches_local_key_signer_for_legacy_tx():
    w3 = _w3()
    local = LocalKeySigner(TEST_KEY, w3)
    kms = AwsKmsSigner("fake-key-id", w3, client=FakeAwsKmsClient(TEST_KEY_HEX))

    local_signed = local.sign_transaction(dict(LEGACY_TX))
    kms_signed = kms.sign_transaction(dict(LEGACY_TX))

    assert kms_signed.raw_transaction == local_signed.raw_transaction
    assert (kms_signed.v, kms_signed.r, kms_signed.s) == (local_signed.v, local_signed.r, local_signed.s)


def test_gcp_kms_signer_matches_local_key_signer_for_dynamic_fee_tx():
    w3 = _w3()
    local = LocalKeySigner(TEST_KEY, w3)
    kms = GcpKmsSigner("fake/key/version", w3, client=FakeGcpKmsClient(TEST_KEY_HEX))

    assert kms.address == local.address

    local_signed = local.sign_transaction(dict(DYNAMIC_FEE_TX))
    kms_signed = kms.sign_transaction(dict(DYNAMIC_FEE_TX))

    assert kms_signed.raw_transaction == local_signed.raw_transaction
    assert (kms_signed.v, kms_signed.r, kms_signed.s) == (local_signed.v, local_signed.r, local_signed.s)


def test_gcp_kms_signer_matches_local_key_signer_for_legacy_tx():
    w3 = _w3()
    local = LocalKeySigner(TEST_KEY, w3)
    kms = GcpKmsSigner("fake/key/version", w3, client=FakeGcpKmsClient(TEST_KEY_HEX))

    local_signed = local.sign_transaction(dict(LEGACY_TX))
    kms_signed = kms.sign_transaction(dict(LEGACY_TX))

    assert kms_signed.raw_transaction == local_signed.raw_transaction
    assert (kms_signed.v, kms_signed.r, kms_signed.s) == (local_signed.v, local_signed.r, local_signed.s)


def test_kms_signer_address_matches_known_anvil_account_for_that_key():
    # Cross-check against a second, independently-known key/address pair
    # (Anvil's well-known account #0) so the address-derivation path is
    # verified against a value that isn't just "whatever eth_account
    # computed in this same test run".
    anvil_key_hex = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    w3 = _w3()
    kms = AwsKmsSigner("fake-key-id", w3, client=FakeAwsKmsClient(anvil_key_hex))
    assert kms.address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_aws_kms_signer_requires_boto3_when_no_client_given():
    import pytest
    w3 = _w3()
    # boto3 isn't a base dependency (see signer.py's module docstring) —
    # constructing without an injected client should either succeed (if
    # boto3 happens to be installed) or fail with a clear ImportError, not
    # some unrelated AttributeError/ModuleNotFoundError deep in a traceback.
    try:
        import boto3  # noqa: F401
        pytest.skip("boto3 is installed in this environment — nothing to assert here")
    except ImportError:
        pass
    try:
        AwsKmsSigner("fake-key-id", w3)
        assert False, "expected ImportError when boto3 is not installed"
    except ImportError as e:
        assert "boto3" in str(e)
