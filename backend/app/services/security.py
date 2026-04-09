import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_fernet = None


def _get_fernet_class():
    from cryptography.fernet import Fernet

    return Fernet


def _load_or_create_local_key() -> str:
    # Keep a stable development key on disk when ENCRYPTION_KEY is not set.
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

# In production, this key would be stored in a Secret Manager
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
    ENCRYPTION_KEY = _load_or_create_local_key()


def _get_fernet():
    global _fernet

    if _fernet is None:
        _fernet = _get_fernet_class()(ENCRYPTION_KEY.encode())
    return _fernet

def encrypt_token(token: str) -> str:
    if not token:
        return ""
    return _get_fernet().encrypt(token.encode()).decode()

def decrypt_token(token: str) -> str:
    if not token:
        return ""
    return _get_fernet().decrypt(token.encode()).decode()
