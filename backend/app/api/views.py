"""Saved view CRUD.

Each member can create their own private views; owners/admins can
publish workspace-wide ones. `is_default=1` per (workspace, user) — the
dashboard reads the user's default first, then their non-default views,
then any workspace-shared views.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app import models
from app.api.auth import get_current_user
from app.database import get_db

router = APIRouter()


VALID_VISIBILITY = {"private", "workspace"}


class ViewCreate(BaseModel):
    name: str
    config: Dict[str, Any] = {}
    visibility: str = "private"
    is_default: bool = False


class ViewUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    visibility: Optional[str] = None
    is_default: Optional[bool] = None


def _serialize(v: models.SavedView) -> dict:
    return {
        "id": v.id,
        "workspace_id": v.workspace_id,
        "user_id": v.user_id,
        "name": v.name,
        "visibility": v.visibility,
        "is_default": bool(v.is_default),
        "config": v.config or {},
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


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


@router.get("/workspaces/{workspace_id}/views")
def list_views(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)
    rows = (
        db.query(models.SavedView)
        .filter(
            models.SavedView.workspace_id == workspace_id,
            or_(
                models.SavedView.user_id == user_id,
                models.SavedView.visibility == "workspace",
            ),
        )
        .order_by(
            models.SavedView.is_default.desc(),
            models.SavedView.created_at.asc(),
        )
        .all()
    )
    return [_serialize(v) for v in rows]


def _clear_other_defaults(db: Session, workspace_id: int, user_id: str, except_id: Optional[int] = None) -> None:
    q = (
        db.query(models.SavedView)
        .filter(
            models.SavedView.workspace_id == workspace_id,
            models.SavedView.user_id == user_id,
            models.SavedView.is_default == 1,
        )
    )
    if except_id is not None:
        q = q.filter(models.SavedView.id != except_id)
    for row in q.all():
        row.is_default = 0


@router.post("/workspaces/{workspace_id}/views")
def create_view(
    workspace_id: int,
    body: ViewCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = _require_membership(db, workspace_id, user_id)
    if body.visibility not in VALID_VISIBILITY:
        raise HTTPException(status_code=400, detail=f"visibility must be in {sorted(VALID_VISIBILITY)}.")
    if body.visibility == "workspace" and m.role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Only owners and admins can publish workspace-shared views.",
        )

    v = models.SavedView(
        workspace_id=workspace_id,
        user_id=user_id,
        name=body.name,
        visibility=body.visibility,
        config=body.config or {},
        is_default=1 if body.is_default else 0,
    )
    db.add(v)
    db.flush()
    if body.is_default:
        _clear_other_defaults(db, workspace_id, user_id, except_id=v.id)
    db.commit()
    db.refresh(v)
    return _serialize(v)


@router.patch("/workspaces/{workspace_id}/views/{view_id}")
def update_view(
    workspace_id: int,
    view_id: int,
    body: ViewUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = _require_membership(db, workspace_id, user_id)
    v = (
        db.query(models.SavedView)
        .filter(
            models.SavedView.id == view_id,
            models.SavedView.workspace_id == workspace_id,
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="View not found.")

    # Only the author or an owner/admin can edit the row. Workspace-shared
    # views can be tweaked by any admin; private views are author-only.
    if v.user_id != user_id and m.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Cannot edit this view.")

    if body.visibility is not None:
        if body.visibility not in VALID_VISIBILITY:
            raise HTTPException(status_code=400, detail="Invalid visibility.")
        if body.visibility == "workspace" and m.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Promotion to workspace requires admin.")
        v.visibility = body.visibility
    if body.name is not None: v.name = body.name
    if body.config is not None: v.config = body.config
    if body.is_default is not None:
        v.is_default = 1 if body.is_default else 0
        if body.is_default:
            _clear_other_defaults(db, workspace_id, user_id, except_id=v.id)

    db.commit()
    db.refresh(v)
    return _serialize(v)


@router.delete("/workspaces/{workspace_id}/views/{view_id}")
def delete_view(
    workspace_id: int,
    view_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = _require_membership(db, workspace_id, user_id)
    v = (
        db.query(models.SavedView)
        .filter(
            models.SavedView.id == view_id,
            models.SavedView.workspace_id == workspace_id,
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="View not found.")
    if v.user_id != user_id and m.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Cannot delete this view.")
    db.delete(v)
    db.commit()
    return {"status": "deleted", "id": view_id}
