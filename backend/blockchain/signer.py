"""
blockchain/signer.py — pluggable transaction signing.

LocalKeySigner wraps the same `w3.eth.account.sign_transaction` call
Phase 1's BlockchainBridge used, keyed off config's private_key — fine for
local dev, never for a production key with real value behind it.

AwsKmsSigner / GcpKmsSigner hold NO private key material in process
memory at all: the raw secp256k1 private key stays inside the cloud KMS
and never leaves it. Both implement the same Signer protocol, so nothing
that calls `signer.sign_transaction(tx)` (anchor_worker/submit.py,
blockchain/score_writer.py) needs to change to use one — that's the whole
point of depending on the protocol rather than "there is a private key in
an env var".

## Why this is more than "call KMS.sign()"

Cloud KMS signs an arbitrary digest with ECDSA and hands back a DER-
encoded (r, s) — nothing Ethereum-specific. Turning that into a valid,
broadcastable signed transaction requires:

  1. Building the exact same RLP/typed-transaction unsigned payload and
     hash eth_account itself would build (`serializable_unsigned_
     transaction_from_dict(...).hash()`), so the digest signed is the
     digest the network will recompute and expect a matching signature
     for.
  2. Canonicalizing `s` to the lower half of the curve order. KMS has no
     concept of Ethereum's low-s rule (EIP-2 / Homestead); an
     uncanonicalized high-s signature is cryptographically valid but the
     network rejects the transaction outright. Flipping s = n - s also
     flips which of the two recovery ids is correct — see
     `_normalize_low_s`.
  3. Recovering `v` (0 or 1): KMS's Sign API doesn't return a recovery
     id, only (r, s). The public key is known (from GetPublicKey), so the
     fix is to try both candidate recovery ids and keep whichever one
     recovers back to that public key — see `_recover_id`.
  4. Encoding `v` correctly for the transaction *type*: legacy
     transactions fold the recovery id into `v` via the EIP-155 formula
     (`recovery_id + chain_id*2 + 35`); typed transactions (EIP-1559
     etc.) use the raw 0/1 recovery id directly, with chain id carried in
     its own field instead. Getting this wrong produces a transaction
     that fails signature recovery on-chain even though every other byte
     is correct — see `_v_for_unsigned_tx`.

This mirrors exactly what `eth_account.account.Account.sign_transaction`
does internally (see `eth_account._utils.signing.sign_transaction_dict`),
substituting the cloud KMS's `Sign` call for local `PrivateKey.sign_msg_
hash`. Verified in tests/test_kms_signer.py by signing the SAME
transaction (both an EIP-1559 dynamic-fee tx and a legacy gasPrice tx)
via LocalKeySigner and via each KMS signer (backed by a fake KMS client
wrapping that same key, so the signer code never sees the raw key) and
asserting the resulting raw transaction bytes are byte-identical — the
strongest check possible without a real AWS/GCP account, since it proves
the whole hash -> sign -> recover -> encode pipeline matches eth_account's
own reference behavior exactly, not just "looks plausible".
"""

from typing import Optional, Protocol

from eth_account._utils.legacy_transactions import (
    Transaction,
    UnsignedTransaction,
    encode_transaction,
    serializable_unsigned_transaction_from_dict,
)
from eth_account.datastructures import SignedTransaction
from eth_account.typed_transactions import TypedTransaction
from eth_keys.constants import SECPK1_N
from eth_keys.datatypes import Signature
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3


class Signer(Protocol):
    @property
    def address(self) -> str: ...

    def sign_transaction(self, transaction: dict) -> SignedTransaction: ...


class LocalKeySigner:
    """Signs with a raw private key held in process memory. Fine for local
    dev and this phase's Anvil/testnet scope; never point this at a
    production signing key with real value behind it."""

    def __init__(self, private_key: str, w3: Web3):
        self._w3 = w3
        self._account = w3.eth.account.from_key(private_key)
        self._private_key = private_key

    @property
    def address(self) -> str:
        return self._account.address

    def sign_transaction(self, transaction: dict) -> SignedTransaction:
        return self._w3.eth.account.sign_transaction(transaction, self._private_key)


# ─────────────────────────────────────────────────────────────────────────────
#  Shared crypto: DER signature -> canonical (r, s) -> recovered v -> encoding
#  Used by both AwsKmsSigner and GcpKmsSigner — the reconstruction math is
#  identical, only the client calls that produce the DER signature/public
#  key bytes differ between providers.
# ─────────────────────────────────────────────────────────────────────────────

def _der_to_rs(der_signature: bytes) -> tuple[int, int]:
    """Decode a DER-encoded ECDSA signature (what both AWS KMS's Sign API
    and GCP KMS's AsymmetricSign API return) into raw (r, s) integers."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(der_signature)
    return r, s


def _normalize_low_s(s: int) -> int:
    """Ethereum requires s <= n/2 (EIP-2). ECDSA signatures are malleable —
    (r, s) and (r, n-s) both verify against the same digest and public key
    — so flipping to the low-s form doesn't invalidate the signature, it
    just picks the canonical one the network will accept. Whichever
    recovery id was correct for s is NOT correct for n-s; callers must
    recompute the recovery id against whichever s they end up using
    (`_recover_id` is called with the already-normalized s)."""
    return SECPK1_N - s if s > SECPK1_N // 2 else s


def _recover_id(digest: bytes, r: int, s: int, expected_address: str) -> int:
    """KMS returns (r, s) with no recovery id. Try both candidates (0, 1)
    and keep whichever one recovers back to the KMS key's known address —
    exactly the brute-force every KMS-backed Ethereum signer does, since
    Sign() has no Ethereum-specific concept of a recovery id."""
    for candidate in (0, 1):
        sig = Signature(vrs=(candidate, r, s))
        recovered = sig.recover_public_key_from_msg_hash(digest).to_checksum_address()
        if recovered.lower() == expected_address.lower():
            return candidate
    raise ValueError(
        f"could not recover a valid signature for {expected_address} — "
        "the digest, (r, s), or public key don't correspond to each other"
    )


def _v_for_unsigned_tx(unsigned_transaction, recovery_id: int) -> int:
    """Fold the raw 0/1 recovery id into the final `v` field, per
    transaction type — see this module's docstring, point 4."""
    if isinstance(unsigned_transaction, TypedTransaction):
        return recovery_id
    if isinstance(unsigned_transaction, Transaction):
        chain_id = unsigned_transaction.v  # EIP-155 packs chain id into the placeholder v
        return recovery_id + chain_id * 2 + 35
    if isinstance(unsigned_transaction, UnsignedTransaction):
        return recovery_id + 27  # pre-EIP-155, no chain id
    raise TypeError(f"unknown unsigned transaction type: {type(unsigned_transaction)}")


def _eth_address_from_der_public_key(der_public_key: bytes) -> str:
    """Derive the Ethereum address from a DER SubjectPublicKeyInfo blob
    (what both AWS's GetPublicKey and GCP's GetPublicKey return, after
    GCP's PEM is decoded to DER) — keccak256 of the uncompressed EC point
    (minus the leading 0x04 byte), last 20 bytes, checksummed."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    public_key = load_der_public_key(der_public_key)
    uncompressed = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return Web3.to_checksum_address(keccak(uncompressed[1:])[-20:])


def _sign_via_kms_digest(transaction: dict, address: str, sign_digest) -> SignedTransaction:
    """Shared signing pipeline for both cloud KMS signers.

    `sign_digest` is a callable (digest: bytes) -> bytes returning a
    DER-encoded ECDSA signature — the one part that's actually
    provider-specific (an AWS or GCP API call), injected by the caller.
    """
    unsigned_transaction = serializable_unsigned_transaction_from_dict(transaction)
    digest = unsigned_transaction.hash()

    der_signature = sign_digest(digest)
    r, s = _der_to_rs(der_signature)
    s = _normalize_low_s(s)
    recovery_id = _recover_id(digest, r, s, address)
    v = _v_for_unsigned_tx(unsigned_transaction, recovery_id)

    encoded_transaction = encode_transaction(unsigned_transaction, vrs=(v, r, s))
    tx_hash = keccak(encoded_transaction)
    return SignedTransaction(
        raw_transaction=HexBytes(encoded_transaction),
        hash=HexBytes(tx_hash),
        r=r, s=s, v=v,
    )


class AwsKmsSigner:
    """Signs with an AWS KMS asymmetric key (KeySpec=ECC_SECG_P256K1,
    KeyUsage=SIGN_VERIFY) — the private key never leaves KMS; this class
    only ever sends it a 32-byte digest and gets back (r, s). Requires
    `pip install boto3` (not a base dependency — most deployments only
    need one signer backend, so this stays an opt-in extra).

    `client`, if given, is used instead of constructing a real boto3 KMS
    client — this is how tests inject a fake KMS without ever exercising
    real AWS, and how a caller could point at a non-default botocore
    session/config.
    """

    def __init__(self, key_id: str, w3: Web3, client=None, region_name: Optional[str] = None):
        if client is None:
            try:
                import boto3
            except ImportError as e:
                raise ImportError(
                    "AwsKmsSigner requires boto3 — pip install boto3"
                ) from e
            client = boto3.client("kms", region_name=region_name)
        self._client = client
        self._key_id = key_id
        self._w3 = w3
        self._address: Optional[str] = None

    @property
    def address(self) -> str:
        if self._address is None:
            response = self._client.get_public_key(KeyId=self._key_id)
            self._address = _eth_address_from_der_public_key(response["PublicKey"])
        return self._address

    def sign_transaction(self, transaction: dict) -> SignedTransaction:
        def sign_digest(digest: bytes) -> bytes:
            response = self._client.sign(
                KeyId=self._key_id,
                Message=digest,
                MessageType="DIGEST",
                SigningAlgorithm="ECDSA_SHA_256",
            )
            return response["Signature"]

        return _sign_via_kms_digest(transaction, self.address, sign_digest)


class VaultKvSigner:
    """Signs with a raw private key fetched from HashiCorp Vault's KV v2
    secrets engine, held in process memory for this signer's lifetime —
    the private key itself never sits in a plaintext .env file or
    docker-compose environment variable, and every fetch is gated by
    Vault's own access policies and recorded in Vault's audit log.

    HONEST LIMITATION vs AwsKmsSigner/GcpKmsSigner above: those never let
    the raw key leave the cloud KMS at all — only a digest goes in, a
    signature comes out. Vault's OSS Transit secrets engine is the
    would-be equivalent ("sign without ever exposing the key"), but its
    supported key types are ed25519/ecdsa-p256/ecdsa-p384/ecdsa-p521/rsa —
    NOT secp256k1, the curve Ethereum uses — as of any current open-source
    Vault release. So this is Vault-backed key CUSTODY (fetch once at
    startup, sign locally after that, same trust boundary as
    LocalKeySigner from that point on), not Vault-backed signing. Still a
    real improvement over a bare env var: centralized secret storage,
    access control, audit trail, and rotation without a redeploy — just
    not the stronger "key never touches this process" guarantee the two
    KMS backends give. Requires `pip install hvac` (opt-in extra).

    `client`, if given, is used instead of constructing a real
    hvac.Client — same testability hook as AwsKmsSigner/GcpKmsSigner.
    """

    def __init__(
        self,
        vault_addr: str,
        vault_token: str,
        secret_path: str,
        w3: Web3,
        client=None,
        mount_point: str = "secret",
        key_field: str = "private_key",
    ):
        if client is None:
            try:
                import hvac
            except ImportError as e:
                raise ImportError("VaultKvSigner requires hvac — pip install hvac") from e
            client = hvac.Client(url=vault_addr, token=vault_token)
            if not client.is_authenticated():
                raise RuntimeError(f"failed to authenticate to Vault at {vault_addr}")

        response = client.secrets.kv.v2.read_secret_version(path=secret_path, mount_point=mount_point)
        private_key = response["data"]["data"][key_field]
        self._local = LocalKeySigner(private_key, w3)

    @property
    def address(self) -> str:
        return self._local.address

    def sign_transaction(self, transaction: dict) -> SignedTransaction:
        return self._local.sign_transaction(transaction)


class GcpKmsSigner:
    """Signs with a GCP Cloud KMS asymmetric key (purpose=ASYMMETRIC_SIGN,
    algorithm=EC_SIGN_SECP256K1_SHA256) — same custody model as
    AwsKmsSigner, just against Cloud KMS's API. Requires
    `pip install google-cloud-kms` (opt-in extra, not a base dependency).

    `key_version_name` is the full resource path, e.g.:
    "projects/P/locations/L/keyRings/R/cryptoKeys/K/cryptoKeyVersions/1"

    `client`, if given, is used instead of constructing a real
    KeyManagementServiceClient — same testability hook as AwsKmsSigner.
    """

    def __init__(self, key_version_name: str, w3: Web3, client=None):
        if client is None:
            try:
                from google.cloud import kms
            except ImportError as e:
                raise ImportError(
                    "GcpKmsSigner requires google-cloud-kms — pip install google-cloud-kms"
                ) from e
            client = kms.KeyManagementServiceClient()
        self._client = client
        self._key_version_name = key_version_name
        self._w3 = w3
        self._address: Optional[str] = None

    @property
    def address(self) -> str:
        if self._address is None:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key, Encoding, PublicFormat

            response = self._client.get_public_key(request={"name": self._key_version_name})
            # GCP returns a PEM-encoded SubjectPublicKeyInfo; re-serialize
            # to DER so the same helper AwsKmsSigner uses can derive the
            # address from it.
            pem_key = load_pem_public_key(response.pem.encode("utf-8"))
            der_key = pem_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            self._address = _eth_address_from_der_public_key(der_key)
        return self._address

    def sign_transaction(self, transaction: dict) -> SignedTransaction:
        def sign_digest(digest: bytes) -> bytes:
            # Cloud KMS's AsymmetricSign expects the pre-hashed digest
            # wrapped as a Digest message naming which hash algorithm
            # produced it (sha256, matching EC_SIGN_SECP256K1_SHA256).
            response = self._client.asymmetric_sign(
                request={
                    "name": self._key_version_name,
                    "digest": {"sha256": digest},
                }
            )
            return response.signature

        return _sign_via_kms_digest(transaction, self.address, sign_digest)
