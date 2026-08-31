"""Budget CRUD + pacing report. Mutations are owner/admin only.

Each row in the response carries the computed `pacing` envelope so the
UI doesn't need a second roundtrip to render the progress bar.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user, record_audit
from app.database import get_db
from app.services.budgets import compute_pacing

router = APIRouter()


VALID_SCOPES = {"workspace", "platform", "connection"}
VALID_PERIODS = {"monthly", "quarterly"}


class BudgetCreate(BaseModel):
    name: str
    scope_type: str  # workspace | platform | connection
    scope_key: Optional[str] = None
    period_type: str  # monthly | quarterly
    amount: str
    start_date: Optional[str] = None  # ISO date; defaults to today
    is_active: bool = True
    alert_at_pct: Optional[int] = 80


class BudgetUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[str] = None
    alert_at_pct: Optional[int] = None
    is_active: Optional[bool] = None


def _serialize(b: models.Budget, pacing: Optional[dict] = None) -> dict:
    return {
        "id": b.id,
        "workspace_id": b.workspace_id,
        "name": b.name,
        "scope_type": b.scope_type,
        "scope_key": b.scope_key,
        "period_type": b.period_type,
        "amount": b.amount,
        "start_date": b.start_date.isoformat() if b.start_date else None,
        "is_active": bool(b.is_active),
        "alert_at_pct": b.alert_at_pct,
        "last_alert_at": b.last_alert_at.isoformat() if b.last_alert_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "pacing": pacing,
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


@router.get("/workspaces/{workspace_id}/budgets")
def list_budgets(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin", "member", "viewer"})
    rows = (
        db.query(models.Budget)
        .filter(models.Budget.workspace_id == workspace_id)
        .order_by(models.Budget.created_at.desc())
        .all()
    )
    return [_serialize(b, compute_pacing(db, b)) for b in rows]


@router.post("/workspaces/{workspace_id}/budgets")
def create_budget(
    workspace_id: int,
    body: BudgetCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    if body.scope_type not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope_type must be in {sorted(VALID_SCOPES)}.")
    if body.period_type not in VALID_PERIODS:
        raise HTTPException(status_code=400, detail=f"period_type must be in {sorted(VALID_PERIODS)}.")
    if body.scope_type != "workspace" and not (body.scope_key or "").strip():
        raise HTTPException(
            status_code=400,
            detail="scope_key is required for scope_type platform or connection.",
        )
    try:
        amount = float(body.amount)
        assert amount > 0
    except (TypeError, ValueError, AssertionError):
        raise HTTPException(status_code=400, detail="amount must be a positive number.")
    if body.alert_at_pct is not None and not (0 <= body.alert_at_pct <= 100):
        raise HTTPException(status_code=400, detail="alert_at_pct must be 0..100.")

    start = (
        datetime.fromisoformat(body.start_date)
        if body.start_date
        else datetime.utcnow()
    )

    b = models.Budget(
        workspace_id=workspace_id,
        name=body.name,
        scope_type=body.scope_type,
        scope_key=body.scope_key if body.scope_type != "workspace" else None,
        period_type=body.period_type,
        amount=body.amount,
        start_date=start,
        is_active=1 if body.is_active else 0,
        alert_at_pct=body.alert_at_pct,
        created_by_subject=user_id,
    )
    db.add(b)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="budget.create",
        target_type="budget",
        target_id=str(b.id),
        payload={"name": body.name, "scope_type": body.scope_type, "amount": body.amount},
    )
    db.commit()
    db.refresh(b)
    return _serialize(b, compute_pacing(db, b))


@router.patch("/workspaces/{workspace_id}/budgets/{budget_id}")
def update_budget(
    workspace_id: int,
    budget_id: int,
    body: BudgetUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    b = (
        db.query(models.Budget)
        .filter(
            models.Budget.id == budget_id,
            models.Budget.workspace_id == workspace_id,
        )
        .first()
    )
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found.")

    if body.amount is not None:
        try:
            assert float(body.amount) > 0
        except (TypeError, ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="amount must be a positive number.")
        b.amount = body.amount
    if body.name is not None: b.name = body.name
    if body.alert_at_pct is not None:
        if not (0 <= body.alert_at_pct <= 100):
            raise HTTPException(status_code=400, detail="alert_at_pct must be 0..100.")
        b.alert_at_pct = body.alert_at_pct
    if body.is_active is not None: b.is_active = 1 if body.is_active else 0

    db.commit()
    db.refresh(b)
    return _serialize(b, compute_pacing(db, b))


@router.delete("/workspaces/{workspace_id}/budgets/{budget_id}")
def delete_budget(
    workspace_id: int,
    budget_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})
    b = (
        db.query(models.Budget)
        .filter(
            models.Budget.id == budget_id,
            models.Budget.workspace_id == workspace_id,
        )
        .first()
    )
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found.")
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="budget.delete",
        target_type="budget",
        target_id=str(budget_id),
        payload={"name": b.name},
    )
    db.delete(b)
    db.commit()
    return {"status": "deleted", "id": budget_id}
