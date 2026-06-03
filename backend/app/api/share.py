"""Public read-only report share links.

Two surfaces:
- Authenticated CRUD under `/api/workspaces/{wid}/reports/{rid}/share`
  to mint + revoke links. Owner/admin only.
- Anonymous `GET /api/share/{token}` returns the report payload (same
  shape `GET /api/reports/{id}` produces) without requiring auth — that's
  the whole point. Bumps the view counter on each successful read.

Tokens: 32-byte url-safe random; stored only as sha256 hash. The raw
token is returned exactly once at mint time. Default expiry is 30 days;
caller can override per-link.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user, record_audit
from app.api.endpoints import _serialize_report
from app.database import get_db

router = APIRouter()


class ShareCreate(BaseModel):
    expires_in_days: int = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serialize_link(link: models.ReportShareLink, *, token: Optional[str] = None) -> dict:
    return {
        "id": link.id,
        "report_id": link.report_id,
        "workspace_id": link.workspace_id,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "is_active": bool(link.is_active),
        "view_count": link.view_count,
        "last_viewed_at": link.last_viewed_at.isoformat() if link.last_viewed_at else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        # Raw token returned only at mint time.
        "token": token,
    }


def _require_role(db: Session, workspace_id: int, user_id: str, allowed: set[str]):
    m = (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )
    if not m or m.role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Requires role in {sorted(allowed)} (you are {m.role if m else 'not a member'}).",
        )


@router.post("/workspaces/{workspace_id}/reports/{report_id}/share")
def create_share_link(
    workspace_id: int,
    report_id: int,
    body: ShareCreate = ShareCreate(),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    report = (
        db.query(models.Report)
        .filter(
            models.Report.id == report_id,
            models.Report.workspace_id == workspace_id,
        )
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    if not (1 <= body.expires_in_days <= 365):
        raise HTTPException(status_code=400, detail="expires_in_days must be in 1..365.")

    token = secrets.token_urlsafe(32)
    link = models.ReportShareLink(
        workspace_id=workspace_id,
        report_id=report_id,
        token_hash=_hash_token(token),
        created_by_subject=user_id,
        expires_at=datetime.utcnow() + timedelta(days=body.expires_in_days),
    )
    db.add(link)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="share.create",
        target_type="report_share_link",
        target_id=link.id,
        payload={"report_id": report_id, "expires_in_days": body.expires_in_days},
    )
    db.commit()
    db.refresh(link)
    return _serialize_link(link, token=token)


@router.get("/workspaces/{workspace_id}/reports/{report_id}/share")
def list_share_links(
    workspace_id: int,
    report_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin", "member", "viewer"})
    rows = (
        db.query(models.ReportShareLink)
        .filter(
            models.ReportShareLink.workspace_id == workspace_id,
            models.ReportShareLink.report_id == report_id,
        )
        .order_by(models.ReportShareLink.created_at.desc())
        .all()
    )
    return [_serialize_link(r) for r in rows]


@router.delete("/workspaces/{workspace_id}/share/{link_id}")
def revoke_share_link(
    workspace_id: int,
    link_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})
    link = (
        db.query(models.ReportShareLink)
        .filter(
            models.ReportShareLink.id == link_id,
            models.ReportShareLink.workspace_id == workspace_id,
        )
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found.")
    link.is_active = 0
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="share.revoke",
        target_type="report_share_link",
        target_id=str(link_id),
        payload={"report_id": link.report_id},
    )
    db.commit()
    return {"status": "revoked", "id": link_id}


@router.get("/share/{token}")
def view_public_share(token: str, db: Session = Depends(get_db)):
    """Anonymous: no auth, no workspace header. Used by recipients to
    view the report. 410 Gone for revoked or expired links so the UI
    can show a clear message instead of a generic 404."""
    link = (
        db.query(models.ReportShareLink)
        .filter(models.ReportShareLink.token_hash == _hash_token(token))
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found.")
    if not link.is_active:
        raise HTTPException(status_code=410, detail="Share link has been revoked.")
    if link.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Share link has expired.")

    report = (
        db.query(models.Report)
        .filter(models.Report.id == link.report_id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Underlying report no longer exists.")

    link.view_count += 1
    link.last_viewed_at = datetime.utcnow()
    db.commit()

    return {
        "report": _serialize_report(report),
        "share": {
            "expires_at": link.expires_at.isoformat(),
            "view_count": link.view_count,
        },
    }
