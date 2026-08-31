"""Storage for mirrored ad-creative assets.

The standalone gallery downloaded each creative into Firebase Storage and
called `blob.make_public()`. Mirroring is kept — Meta's creative URLs are
signed and expire, so hotlinking them rots the gallery within days — but the
objects are **not** made public here. They are served workspace-scoped through
`GET /api/creatives/{id}/asset`, which is what closes the "public reads" item
the gallery's own MIGRATION.md lists as still open.

Two backends:

* **GCS** when `CREATIVES_BUCKET` is set, or when a GCP project is resolvable
  (defaulting to that project's `…firebasestorage.app` bucket — the same one
  the standalone pipelines already write to, so this needs no new infra).
* **Local directory** (`CREATIVES_LOCAL_DIR`, default `./.creative-assets`)
  otherwise, so dev and tests need no cloud credentials.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Assets are downloaded from ad platforms. Cap the size so a hostile or
# malformed URL cannot exhaust the worker's memory.
MAX_ASSET_BYTES = int(os.getenv("CREATIVES_MAX_ASSET_BYTES", str(25 * 1024 * 1024)))
DOWNLOAD_TIMEOUT_SECONDS = 30.0

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
}


class CreativeAssetError(Exception):
    """Asset could not be stored. Never fatal to a sync — the creative is kept
    with its text and no mirrored asset."""


def _project_id() -> Optional[str]:
    return (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("FIREBASE_PROJECT_ID")
    )


def bucket_name() -> Optional[str]:
    """Resolve the creatives bucket, or None to use the local backend."""
    explicit = os.getenv("CREATIVES_BUCKET", "").strip()
    if explicit:
        return explicit
    if os.getenv("CREATIVES_FORCE_LOCAL", "").strip().lower() in {"1", "true", "yes"}:
        return None
    project = _project_id()
    return f"{project}.firebasestorage.app" if project else None


def _local_dir() -> Path:
    return Path(os.getenv("CREATIVES_LOCAL_DIR", ".creative-assets")).resolve()


def _extension(url: str, content_type: str) -> str:
    ext = _EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
    if ext:
        return ext
    # Fall back to the URL's own suffix, ignoring any query string.
    tail = url.lower().split("?")[0].rsplit(".", 1)
    if len(tail) == 2 and 1 <= len(tail[1]) <= 4 and tail[1].isalnum():
        return tail[1]
    return "jpg"


def asset_path(platform: str, workspace_id: int, ad_id: str, ext: str) -> str:
    # Ad IDs come from platform APIs and are numeric in practice, but they
    # land in an object path, so anything path-significant is stripped.
    safe_ad_id = "".join(c for c in str(ad_id) if c.isalnum() or c in "-_")[:120] or "unknown"
    return f"creatives/{platform}/{workspace_id}/{safe_ad_id}.{ext}"


def _gcs_bucket():
    from google.cloud import storage  # type: ignore

    return storage.Client(project=_project_id()).bucket(bucket_name())


def exists(path: str) -> bool:
    if bucket_name():
        try:
            return _gcs_bucket().blob(path).exists()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not stat creative asset %s: %s", path, exc)
            return False
    return (_local_dir() / path).exists()


def _write(path: str, data: bytes, content_type: str) -> None:
    if bucket_name():
        _gcs_bucket().blob(path).upload_from_string(data, content_type=content_type)
        return
    target = _local_dir() / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


async def mirror_asset(
    source_url: Optional[str],
    platform: str,
    workspace_id: int,
    ad_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Download a creative asset once and store it.

    Returns `(asset_path, content_type)`, or `(None, None)` when there is
    nothing to store or the download failed. Failures are logged and swallowed:
    a broken image must not fail a whole platform sync, which is how the source
    pipelines behaved too.
    """
    if not source_url or not source_url.startswith("http"):
        return None, None

    # Skip-if-present, matching the source pipeline. The guessed extension is
    # only a probe; a hit means the bytes are already stored.
    for candidate_ext in ("jpg", "png", "gif", "webp", "mp4"):
        candidate = asset_path(platform, workspace_id, ad_id, candidate_ext)
        if exists(candidate):
            return candidate, _content_type_for_ext(candidate_ext)

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
            data = resp.content
            if len(data) > MAX_ASSET_BYTES:
                logger.warning(
                    "Creative asset for %s/%s is %d bytes, over the %d limit — not stored.",
                    platform, ad_id, len(data), MAX_ASSET_BYTES,
                )
                return None, None
            content_type = resp.headers.get("content-type", "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to download creative asset for %s/%s: %s", platform, ad_id, exc)
        return None, None

    ext = _extension(source_url, content_type)
    path = asset_path(platform, workspace_id, ad_id, ext)
    try:
        await _to_thread_write(path, data, content_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to store creative asset for %s/%s: %s", platform, ad_id, exc)
        return None, None
    return path, content_type


async def _to_thread_write(path: str, data: bytes, content_type: str) -> None:
    import asyncio

    await asyncio.to_thread(_write, path, data, content_type)


def _content_type_for_ext(ext: str) -> str:
    for ctype, candidate in _EXT_BY_CONTENT_TYPE.items():
        if candidate == ext:
            return ctype
    return "application/octet-stream"


def signed_url(path: str, expires_seconds: int = 900) -> Optional[str]:
    """A short-lived read URL for a stored asset, or None on the local backend
    (where the API streams the bytes itself instead)."""
    if not bucket_name():
        return None
    from datetime import timedelta

    try:
        return _gcs_bucket().blob(path).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_seconds),
            method="GET",
        )
    except Exception as exc:  # noqa: BLE001
        # Signing needs a service-account key or the IAM signBlob permission;
        # on a bare runtime it can fail. The API falls back to streaming.
        logger.warning("Could not sign URL for %s: %s", path, exc)
        return None


def read_bytes(path: str) -> Optional[bytes]:
    """Read a stored asset. Used by the API when signing is unavailable."""
    try:
        if bucket_name():
            return _gcs_bucket().blob(path).download_as_bytes()
        target = _local_dir() / path
        resolved = target.resolve()
        # Defence in depth: asset_path() already sanitises, but never read
        # outside the asset directory.
        if not str(resolved).startswith(str(_local_dir())):
            return None
        return resolved.read_bytes() if resolved.exists() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read creative asset %s: %s", path, exc)
        return None
