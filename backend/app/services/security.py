"""Token encryption for stored OAuth credentials.

Two schemes coexist so we can roll out KMS without re-encrypting historical
data:

- **v2 envelope (preferred)** — when `KMS_KEY_NAME` is set. A fresh 32-byte
  AES-GCM data encryption key (DEK) is generated per token, the token is
  encrypted with the DEK, the DEK is wrapped by a KMS key (the KEK), and the
  ciphertext is stored as `v2:<b64 wrapped_dek>:<b64 nonce||ciphertext>`.
  KMS handles KEK rotation and access logging.

- **Legacy Fernet** — used when KMS isn't configured, and used for any
  ciphertext that doesn't start with `v2:`. Falls back to a local key file
  for offline development. New tokens written under this scheme are tagged
  `v1:<b64 fernet>` for clarity, though unprefixed historical ciphertext is
  still recognized.

`decrypt_token` dispatches on prefix; `encrypt_token` picks the best
available scheme. Rotation = re-encrypt any v1/unprefixed token through
`encrypt_token(decrypt_token(stored))` once KMS is live.
"""
from __future__ import annotations

import base64
import os
import secrets as _secrets
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── Legacy Fernet path ──────────────────────────────────────────────────────

_fernet = None


def _get_fernet_class():
    from cryptography.fernet import Fernet

    return Fernet


def _load_or_create_local_key() -> str:
    key_path = Path(__file__).resolve().parents[2] / ".local_encryption_key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    key = _get_fernet_class().generate_key().decode()
    key_path.write_text(key, encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except Exception:
        pass
    print(
        "WARNING: ENCRYPTION_KEY not found. Generated persistent local key at "
        f"{key_path}. Keep this file to preserve access to stored OAuth tokens."
    )
    return key


# Read via secrets_manager so the legacy Fernet key can also live in Secret Manager.
def _legacy_key() -> str:
    from app.services.secrets_manager import get_secret

    key = get_secret("ENCRYPTION_KEY")
    if not key:
        key = _load_or_create_local_key()
    return key


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet_class()(_legacy_key().encode())
    return _fernet


# ── v2 envelope path (KMS-wrapped DEK + AES-GCM) ────────────────────────────

_kms_client = None
_kms_load_error: Optional[str] = None


def _kms_key_name() -> str:
    """Fully-qualified KMS key resource, e.g.
    `projects/p/locations/l/keyRings/r/cryptoKeys/k`."""
    return os.getenv("KMS_KEY_NAME", "").strip()


def _get_kms_client():
    global _kms_client, _kms_load_error
    if _kms_client is not None or _kms_load_error is not None:
        return _kms_client
    try:
        from google.cloud import kms  # type: ignore

        _kms_client = kms.KeyManagementServiceClient()
    except Exception as exc:  # noqa: BLE001
        _kms_load_error = str(exc)
    return _kms_client


def _kms_available() -> bool:
    return bool(_kms_key_name()) and _get_kms_client() is not None


def _kms_wrap(dek: bytes) -> bytes:
    client = _get_kms_client()
    assert client is not None  # _kms_available() must be true before calling
    resp = client.encrypt(request={"name": _kms_key_name(), "plaintext": dek})
    return resp.ciphertext


def _kms_unwrap(wrapped: bytes) -> bytes:
    client = _get_kms_client()
    if client is None:
        raise RuntimeError(
            f"KMS client unavailable for v2 token decrypt ({_kms_load_error})."
        )
    resp = client.decrypt(request={"name": _kms_key_name(), "ciphertext": wrapped})
    return resp.plaintext


def _aesgcm_encrypt(dek: bytes, plaintext: bytes) -> bytes:
    """Return nonce(12 bytes) || ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = _secrets.token_bytes(12)
    return nonce + AESGCM(dek).encrypt(nonce, plaintext, associated_data=None)


def _aesgcm_decrypt(dek: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, ct = blob[:12], blob[12:]
    return AESGCM(dek).decrypt(nonce, ct, associated_data=None)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))


# ── Public API ──────────────────────────────────────────────────────────────


def encrypt_token(token: str) -> str:
    if not token:
        return ""
    if _kms_available():
        dek = _secrets.token_bytes(32)
        ct = _aesgcm_encrypt(dek, token.encode("utf-8"))
        wrapped = _kms_wrap(dek)
        return f"v2:{_b64e(wrapped)}:{_b64e(ct)}"
    # Legacy path
    return "v1:" + _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    if not token:
        return ""

    if token.startswith("v2:"):
        try:
            _, wrapped_b64, ct_b64 = token.split(":", 2)
        except ValueError as exc:
            raise ValueError("Malformed v2 token") from exc
        dek = _kms_unwrap(_b64d(wrapped_b64))
        return _aesgcm_decrypt(dek, _b64d(ct_b64)).decode("utf-8")

    if token.startswith("v1:"):
        return _get_fernet().decrypt(token[3:].encode()).decode()

    # Unprefixed historical ciphertext (pre-versioning) — assume Fernet.
    return _get_fernet().decrypt(token.encode()).decode()
