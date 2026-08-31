"""Ad creative gallery: list, review, sync, and asset serving.

Replaces the standalone gallery's direct-to-Firestore reads and its
unauthenticated sync Cloud Functions. Every route here is workspace-membership
gated, which is what closes the "public reads" and "unauthenticated functions"
items the source app's MIGRATION.md left open.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user
from app.database import get_db
from app.services import creative_assets
from app.services.creatives import SUPPORTED_PLATFORMS

router = APIRouter()

VALID_REVIEW_STATUSES = {"keep", "remove", "change"}
MAX_PAGE_SIZE = 200


class ReviewUpdate(BaseModel):
    review_status: Optional[str] = None
    review_comment: Optional[str] = None


class CreativeSyncRequest(BaseModel):
    connection_ids: Optional[List[int]] = None


def _require_membership(db: Session, workspace_id: int, user_id: str):
    m = (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )
    if not m:
        raise HTTPException(status_code=403, detail="Not a member of this workspace.")
    return m


def _serialize(c: models.AdCreative) -> Dict[str, Any]:
    return {
        "id": c.id,
        "workspace_id": c.workspace_id,
        "connection_id": c.connection_id,
        "platform": c.platform,
        "ad_id": c.ad_id,
        "account_id": c.account_id,
        "ad_name": c.ad_name,
        "headline": c.headline,
        "ad_text": c.ad_text,
        "campaign_name": c.campaign_name,
        "ad_group_name": c.ad_group_name,
        "creative_type": c.creative_type,
        "group_type": c.group_type,
        "final_url": c.final_url,
        # The mirrored asset is served through this API, never linked directly
        # from the bucket — the objects are private.
        "asset_url": f"/api/workspaces/{c.workspace_id}/creatives/{c.id}/asset" if c.asset_path else None,
        "embed_url": c.embed_url,
        "review_status": c.review_status,
        "review_comment": c.review_comment,
        "review_updated_at": c.review_updated_at.isoformat() if c.review_updated_at else None,
        "review_updated_by": c.review_updated_by,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("/workspaces/{workspace_id}/creatives")
def list_creatives(
    workspace_id: int,
    platform: Optional[str] = Query(None, description="meta | google | microsoft"),
    review_status: Optional[str] = Query(
        None, description="keep | remove | change | unreviewed"
    ),
    search: Optional[str] = Query(None, description="matches ad name, headline, or body"),
    limit: int = Query(60, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)

    q = db.query(models.AdCreative).filter(models.AdCreative.workspace_id == workspace_id)

    if platform:
        key = platform.strip().lower()
        if key not in SUPPORTED_PLATFORMS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown platform '{platform}'. Supported: {', '.join(SUPPORTED_PLATFORMS)}.",
            )
        q = q.filter(models.AdCreative.platform == key)

    if review_status:
        key = review_status.strip().lower()
        if key == "unreviewed":
            q = q.filter(models.AdCreative.review_status.is_(None))
        elif key in VALID_REVIEW_STATUSES:
            q = q.filter(models.AdCreative.review_status == key)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown review_status '{review_status}'.",
            )

    if search:
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                models.AdCreative.ad_name.ilike(pattern),
                models.AdCreative.headline.ilike(pattern),
                models.AdCreative.ad_text.ilike(pattern),
                models.AdCreative.campaign_name.ilike(pattern),
            )
        )

    total = q.count()
    rows = (
        q.order_by(models.AdCreative.updated_at.desc(), models.AdCreative.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "creatives": [_serialize(c) for c in rows],
    }


@router.get("/workspaces/{workspace_id}/creatives/summary")
def creatives_summary(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Counts by platform and review status, for the gallery's filter chips."""
    _require_membership(db, workspace_id, user_id)
    rows = (
        db.query(models.AdCreative)
        .filter(models.AdCreative.workspace_id == workspace_id)
        .all()
    )
    by_platform: Dict[str, int] = {}
    by_review: Dict[str, int] = {"unreviewed": 0, "keep": 0, "remove": 0, "change": 0}
    for c in rows:
        by_platform[c.platform] = by_platform.get(c.platform, 0) + 1
        by_review[c.review_status or "unreviewed"] = by_review.get(c.review_status or "unreviewed", 0) + 1
    return {"total": len(rows), "by_platform": by_platform, "by_review_status": by_review}


@router.patch("/workspaces/{workspace_id}/creatives/{creative_id}/review")
def set_review(
    workspace_id: int,
    creative_id: int,
    payload: ReviewUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set or clear a creative's review decision.

    This is the only writer of the review_* columns; the sync deliberately
    never touches them, so a decision survives every re-sync.
    """
    _require_membership(db, workspace_id, user_id)

    creative = (
        db.query(models.AdCreative)
        .filter(
            models.AdCreative.id == creative_id,
            models.AdCreative.workspace_id == workspace_id,
        )
        .first()
    )
    if not creative:
        raise HTTPException(status_code=404, detail="Creative not found.")

    if payload.review_status is not None:
        status = payload.review_status.strip().lower()
        if status in ("", "none", "unreviewed"):
            creative.review_status = None
        elif status in VALID_REVIEW_STATUSES:
            creative.review_status = status
        else:
            raise HTTPException(
                status_code=400,
                detail=f"review_status must be one of {sorted(VALID_REVIEW_STATUSES)}, or empty to clear.",
            )

    if payload.review_comment is not None:
        creative.review_comment = payload.review_comment.strip() or None

    creative.review_updated_at = datetime.utcnow()
    creative.review_updated_by = user_id
    db.commit()
    db.refresh(creative)
    return _serialize(creative)


@router.post("/workspaces/{workspace_id}/creatives/sync")
async def sync_creatives(
    workspace_id: int,
    payload: Optional[CreativeSyncRequest] = None,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Queue a creative pull and return the SyncJob id to poll.

    Replaces the gallery's three per-platform HTTP functions with one
    authenticated, workspace-scoped trigger.
    """
    from app.services.creative_sync import eligible_connections
    from app.services.task_queue import enqueue

    _require_membership(db, workspace_id, user_id)

    connections = eligible_connections(db, workspace_id)
    if payload and payload.connection_ids:
        wanted = set(payload.connection_ids)
        connections = [c for c in connections if c.id in wanted]
    if not connections:
        raise HTTPException(
            status_code=400,
            detail=(
                "No active Meta, Google Ads or Microsoft Ads connection in this "
                "workspace to pull creatives from."
            ),
        )

    job = models.SyncJob(
        workspace_id=workspace_id,
        user_id=user_id,
        status="pending",
        progress_percent=0,
        current_step="queued",
        total_accounts=len(connections),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    task_id = await enqueue(
        "sync_creatives",
        {
            "sync_job_id": job.id,
            "connection_ids": [c.id for c in connections],
        },
    )
    return {"status": "enqueued", "sync_job_id": job.id, "task_id": task_id}


@router.get("/workspaces/{workspace_id}/creatives/{creative_id}/asset")
def get_asset(
    workspace_id: int,
    creative_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Serve a mirrored creative asset.

    Streams by default. The frontend must fetch this with an auth header and
    render the result as a blob URL, so a 302 to a signed GCS URL would need
    CORS configured on the bucket to be usable — opt into that with
    CREATIVES_SIGNED_URL_REDIRECT=1 once the bucket allows the app origin.
    """
    _require_membership(db, workspace_id, user_id)

    creative = (
        db.query(models.AdCreative)
        .filter(
            models.AdCreative.id == creative_id,
            models.AdCreative.workspace_id == workspace_id,
        )
        .first()
    )
    if not creative or not creative.asset_path:
        raise HTTPException(status_code=404, detail="No stored asset for this creative.")

    if os.getenv("CREATIVES_SIGNED_URL_REDIRECT", "").strip().lower() in {"1", "true", "yes"}:
        signed = creative_assets.signed_url(creative.asset_path)
        if signed:
            return RedirectResponse(signed, status_code=302)

    data = creative_assets.read_bytes(creative.asset_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Stored asset is no longer readable.")
    return Response(
        content=data,
        media_type=creative.asset_content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )
