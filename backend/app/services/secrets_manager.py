"""Secret resolution with Google Secret Manager + env-var fallback.

Use `get_secret("META_CLIENT_SECRET")` instead of `os.getenv("META_CLIENT_SECRET")`
for anything sensitive. In production with `GCP_PROJECT_ID` set, the secret is
fetched from Secret Manager (`projects/{project}/secrets/{name}/versions/latest`)
and cached in-process. Locally, it falls back to the env var so `.env` files
keep working.

Secret naming convention: the secret ID in Secret Manager matches the env-var
name exactly (e.g. `META_CLIENT_SECRET`). Override per-secret with the
optional `SECRET_PREFIX` env var if you want a workspace prefix.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()

_sm_client = None
_sm_load_error: Optional[str] = None


def _project_id() -> Optional[str]:
    return (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("FIREBASE_PROJECT_ID")
    )


def _secret_name(name: str) -> str:
    prefix = os.getenv("SECRET_PREFIX", "").strip()
    return f"{prefix}{name}" if prefix else name


def _get_sm_client():
    """Lazily build the Secret Manager client; cache success or failure."""
    global _sm_client, _sm_load_error
    if _sm_client is not None or _sm_load_error is not None:
        return _sm_client
    try:
        from google.cloud import secretmanager  # type: ignore

        _sm_client = secretmanager.SecretManagerServiceClient()
    except Exception as exc:  # noqa: BLE001
        _sm_load_error = str(exc)
    return _sm_client


def _fetch_from_secret_manager(name: str) -> Optional[str]:
    project = _project_id()
    if not project:
        return None
    client = _get_sm_client()
    if client is None:
        return None
    try:
        resource = f"projects/{project}/secrets/{_secret_name(name)}/versions/latest"
        response = client.access_secret_version(request={"name": resource})
        return response.payload.data.decode("utf-8")
    except Exception:
        # Secret missing or permission denied — fall through to env var.
        return None


def get_secret(name: str, default: str = "") -> str:
    """Resolve a secret by name. Returns the value or `default` if absent."""
    with _cache_lock:
        if name in _cache:
            return _cache[name]

    value = _fetch_from_secret_manager(name)
    if value is None:
        value = os.getenv(name, default)

    with _cache_lock:
        _cache[name] = value
    return value


def invalidate_cache(name: Optional[str] = None) -> None:
    """Drop a cached secret (or all of them). Useful after rotation."""
    with _cache_lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)
