"""Creative sync: fetch → mirror assets → upsert, with SyncJob progress.

Replaces the standalone gallery's three unauthenticated HTTP Cloud Functions
and its `pipeline_status` Firestore document. Progress is reported on the
existing `SyncJob` row, so creative syncs show up in the same monitor as every
other sync in this app.

The upsert is the important part. Sync-owned columns are overwritten on every
run; the review_* columns are never touched here. That is what makes a re-sync
preserve a reviewer's Keep/Remove/Change decision — the Postgres equivalent of
the source pipeline's `batch.set(..., merge=True)`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import models
from app.services import creative_assets, creatives as creative_fetchers

logger = logging.getLogger(__name__)

# Columns the sync owns. Anything outside this set (notably every review_*
# column, and first_seen_at) survives a re-sync untouched.
_SYNC_OWNED_COLUMNS = (
    "account_id",
    "ad_name",
    "headline",
    "ad_text",
    "campaign_name",
    "ad_group_name",
    "creative_type",
    "group_type",
    "final_url",
    "source_asset_url",
    "asset_path",
    "asset_content_type",
    "embed_url",
)


class CreativeSyncError(Exception):
    """A creative sync failure that should surface to the caller."""


def _try_decrypt(token: str) -> str:
    if not token:
        return ""
    from app.services.security import decrypt_token

    try:
        return decrypt_token(token)
    except Exception as exc:  # noqa: BLE001
        raise CreativeSyncError(f"Stored OAuth token can no longer be decrypted ({exc}).")


def eligible_connections(db: Session, workspace_id: int) -> List[models.Connection]:
    """Active connections in this workspace whose platform has creatives."""
    return (
        db.query(models.Connection)
        .filter(
            models.Connection.workspace_id == workspace_id,
            models.Connection.is_active == 1,
            models.Connection.platform.in_(list(creative_fetchers.SUPPORTED_PLATFORMS)),
        )
        .all()
    )


def _upsert(
    db: Session,
    workspace_id: int,
    connection_id: Optional[int],
    record: Dict[str, Any],
) -> bool:
    """Insert or update one creative. Returns True when newly inserted."""
    existing = (
        db.query(models.AdCreative)
        .filter(
            models.AdCreative.workspace_id == workspace_id,
            models.AdCreative.platform == record["platform"],
            models.AdCreative.ad_id == record["ad_id"],
        )
        .first()
    )
    now = datetime.utcnow()

    if existing is None:
        creative = models.AdCreative(
            workspace_id=workspace_id,
            connection_id=connection_id,
            platform=record["platform"],
            ad_id=record["ad_id"],
            first_seen_at=now,
            updated_at=now,
            **{col: record.get(col) for col in _SYNC_OWNED_COLUMNS},
        )
        db.add(creative)
        return True

    existing.connection_id = connection_id
    for col in _SYNC_OWNED_COLUMNS:
        # A failed asset download must not blank out a previously mirrored
        # asset; keep whatever is already stored.
        if col in ("asset_path", "asset_content_type") and record.get(col) is None:
            continue
        setattr(existing, col, record.get(col))
    existing.updated_at = now
    return False


async def sync_connection_creatives(
    db: Session,
    connection: models.Connection,
    *,
    job: Optional[models.SyncJob] = None,
) -> Dict[str, Any]:
    """Sync every selected account on one connection."""
    platform = (connection.platform or "").strip().lower()
    workspace_id = connection.workspace_id

    access_token = _try_decrypt(connection.access_token or "")
    refresh_token = _try_decrypt(connection.refresh_token or "")

    selected = connection.selected_account_ids or []
    accounts = [str(a) for a in selected] if selected else (
        [str(connection.account_id)] if connection.account_id else []
    )
    if not accounts:
        raise CreativeSyncError(
            f"Connection {connection.id} ({platform}) has no accounts selected."
        )

    customer_map = {
        str(a.get("id")): str(a.get("customer_id", ""))
        for a in (connection.available_accounts or [])
    }
    # Same per-connection manager context the performance sync uses.
    google_login_map = {
        str(a.get("id")): str(a.get("login_customer_id") or "")
        for a in (connection.available_accounts or [])
    }
    connection_login_customer_id = str(getattr(connection, "login_customer_id", "") or "")

    created = 0
    updated = 0
    fetched = 0

    for account_id in accounts:
        if job is not None:
            job.current_step = f"{platform}: fetching creatives for {account_id}"
            db.commit()

        records, rotated_refresh_token = await creative_fetchers.fetch_creatives(
            platform,
            account_id,
            access_token=access_token,
            refresh_token=refresh_token,
            microsoft_customer_id=customer_map.get(str(account_id)),
            google_login_customer_id=(
                google_login_map.get(str(account_id), "") or connection_login_customer_id
            ),
        )

        # Microsoft rotates the refresh token on nearly every exchange. Persist
        # it against this workspace's connection — the source pipeline wrote it
        # back to a shared Secret Manager secret, which is precisely why that
        # secret could never be shared with another app.
        if rotated_refresh_token:
            from app.services.security import encrypt_token

            connection.refresh_token = encrypt_token(rotated_refresh_token)
            refresh_token = rotated_refresh_token
            db.commit()

        fetched += len(records)
        if job is not None:
            job.current_step = f"{platform}: storing {len(records)} creatives for {account_id}"
            db.commit()

        for index, record in enumerate(records, start=1):
            if record.get("source_asset_url") and not record.get("embed_url"):
                asset_path, content_type = await creative_assets.mirror_asset(
                    record.get("source_asset_url"),
                    record["platform"],
                    workspace_id,
                    record["ad_id"],
                )
                record["asset_path"] = asset_path
                record["asset_content_type"] = content_type
            else:
                record["asset_path"] = None
                record["asset_content_type"] = None

            if _upsert(db, workspace_id, connection.id, record):
                created += 1
            else:
                updated += 1

            # Commit in batches so a long run makes visible progress and a
            # late failure does not discard everything already downloaded.
            if index % 100 == 0:
                db.commit()

        db.commit()

    connection.last_sync_at = datetime.utcnow()
    db.commit()

    return {
        "connection_id": connection.id,
        "platform": platform,
        "accounts": accounts,
        "fetched": fetched,
        "created": created,
        "updated": updated,
    }


async def run_creatives_sync(
    *,
    db: Session,
    workspace_id: int,
    connection_ids: Optional[List[int]] = None,
    job: Optional[models.SyncJob] = None,
) -> Dict[str, Any]:
    """Sync creatives for some or all eligible connections in a workspace.

    One failing platform does not sink the run — its error is recorded and the
    others continue, matching how the standalone galleries failed independently
    of one another. The run only fails outright when nothing succeeded.
    """
    connections = eligible_connections(db, workspace_id)
    if connection_ids:
        wanted = set(connection_ids)
        connections = [c for c in connections if c.id in wanted]

    if not connections:
        raise CreativeSyncError(
            "No active Meta, Google Ads or Microsoft Ads connection in this workspace to pull creatives from."
        )

    if job is not None:
        job.total_steps = len(connections)
        job.total_accounts = len(connections)
        db.commit()

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for index, connection in enumerate(connections, start=1):
        try:
            results.append(await sync_connection_creatives(db, connection, job=job))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(
                "Creative sync failed for connection %s (%s): %s",
                connection.id, connection.platform, exc,
            )
            errors.append({
                "connection_id": str(connection.id),
                "platform": connection.platform or "unknown",
                "error": str(exc),
            })
        if job is not None:
            job.accounts_synced = index
            job.progress_percent = int(index * 100 / len(connections))
            db.commit()

    if not results and errors:
        raise CreativeSyncError(
            "Creative sync failed for every connection: "
            + "; ".join(f"{e['platform']}: {e['error']}" for e in errors)
        )

    return {
        "synced": sum(r["fetched"] for r in results),
        "created": sum(r["created"] for r in results),
        "updated": sum(r["updated"] for r in results),
        "connections": results,
        "errors": errors,
    }


async def run_creatives_sync_for_job(job_id: int, connection_ids: Optional[List[int]] = None) -> None:
    """Task-handler entrypoint. Owns its own DB session."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        job.current_step = "fetching creatives"
        db.commit()

        try:
            result = await run_creatives_sync(
                db=db,
                workspace_id=job.workspace_id,
                connection_ids=connection_ids,
                job=job,
            )
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.progress_percent = 100
            job.current_step = (
                f"done — {result['created']} new, {result['updated']} updated"
            )
            if result["errors"]:
                job.error_message = "; ".join(
                    f"{e['platform']}: {e['error']}" for e in result["errors"]
                )
            db.commit()
        except CreativeSyncError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error_message = f"Internal error: {exc}"
            job.completed_at = datetime.utcnow()
            db.commit()
            raise
    finally:
        db.close()
