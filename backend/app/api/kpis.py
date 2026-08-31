"""CustomKpi CRUD + evaluation against a specific report.

GET /api/workspaces/{wid}/kpis — list active KPIs for the workspace.
POST/PATCH/DELETE — owner/admin mutations.
GET /api/reports/{rid}/kpis — evaluate every active KPI against that
report's scorecards. Returns one entry per KPI with the computed value
(or error).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import (
    get_current_user,
    get_current_workspace_id,
    record_audit,
)
from app.database import get_db
from app.services.kpi_formula import (
    ALLOWED_VARIABLES,
    FormulaError,
    evaluate,
    validate,
)

router = APIRouter()


VALID_FORMATS = {"currency", "percent", "ratio", "number", "integer"}


class KpiCreate(BaseModel):
    name: str
    formula: str
    format: str = "number"
    description: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True


class KpiUpdate(BaseModel):
    name: Optional[str] = None
    formula: Optional[str] = None
    format: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


def _serialize(k: models.CustomKpi) -> dict:
    return {
        "id": k.id,
        "workspace_id": k.workspace_id,
        "name": k.name,
        "formula": k.formula,
        "format": k.format,
        "description": k.description,
        "sort_order": k.sort_order,
        "is_active": bool(k.is_active),
        "created_by_subject": k.created_by_subject,
        "created_at": k.created_at.isoformat() if k.created_at else None,
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


@router.get("/workspaces/{workspace_id}/kpis")
def list_kpis(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin", "member", "viewer"})
    rows = (
        db.query(models.CustomKpi)
        .filter(models.CustomKpi.workspace_id == workspace_id)
        .order_by(models.CustomKpi.sort_order.asc(), models.CustomKpi.id.asc())
        .all()
    )
    return [_serialize(k) for k in rows]


@router.get("/kpi-variables")
def kpi_variables(_user_id: str = Depends(get_current_user)):
    """Return the allowlist so the UI can render a chip palette."""
    return {"variables": sorted(ALLOWED_VARIABLES), "formats": sorted(VALID_FORMATS)}


@router.post("/workspaces/{workspace_id}/kpis")
def create_kpi(
    workspace_id: int,
    body: KpiCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    if body.format not in VALID_FORMATS:
        raise HTTPException(status_code=400, detail=f"format must be in {sorted(VALID_FORMATS)}.")
    try:
        validate(body.formula)
    except FormulaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    k = models.CustomKpi(
        workspace_id=workspace_id,
        name=body.name,
        formula=body.formula,
        format=body.format,
        description=body.description,
        sort_order=body.sort_order,
        is_active=1 if body.is_active else 0,
        created_by_subject=user_id,
    )
    db.add(k)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="kpi.create",
        target_type="custom_kpi",
        target_id=str(k.id),
        payload={"name": body.name, "formula": body.formula},
    )
    db.commit()
    db.refresh(k)
    return _serialize(k)


@router.patch("/workspaces/{workspace_id}/kpis/{kpi_id}")
def update_kpi(
    workspace_id: int,
    kpi_id: int,
    body: KpiUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    k = (
        db.query(models.CustomKpi)
        .filter(
            models.CustomKpi.id == kpi_id,
            models.CustomKpi.workspace_id == workspace_id,
        )
        .first()
    )
    if not k:
        raise HTTPException(status_code=404, detail="KPI not found.")

    if body.formula is not None:
        try:
            validate(body.formula)
        except FormulaError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        k.formula = body.formula
    if body.format is not None:
        if body.format not in VALID_FORMATS:
            raise HTTPException(status_code=400, detail="Invalid format.")
        k.format = body.format
    if body.name is not None: k.name = body.name
    if body.description is not None: k.description = body.description
    if body.sort_order is not None: k.sort_order = body.sort_order
    if body.is_active is not None: k.is_active = 1 if body.is_active else 0

    db.commit()
    db.refresh(k)
    return _serialize(k)


@router.delete("/workspaces/{workspace_id}/kpis/{kpi_id}")
def delete_kpi(
    workspace_id: int,
    kpi_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})
    k = (
        db.query(models.CustomKpi)
        .filter(
            models.CustomKpi.id == kpi_id,
            models.CustomKpi.workspace_id == workspace_id,
        )
        .first()
    )
    if not k:
        raise HTTPException(status_code=404, detail="KPI not found.")
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="kpi.delete",
        target_type="custom_kpi",
        target_id=str(kpi_id),
        payload={"name": k.name},
    )
    db.delete(k)
    db.commit()
    return {"status": "deleted", "id": kpi_id}


@router.get("/reports/{report_id}/kpis")
def evaluate_kpis_for_report(
    report_id: int,
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Evaluate every active KPI against the report's scorecards.

    Returns one entry per KPI with `value` and an optional `error` —
    bad formulas don't block the response, so the UI can render the
    rest and surface the error inline next to the broken one."""
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

    kpis = (
        db.query(models.CustomKpi)
        .filter(
            models.CustomKpi.workspace_id == workspace_id,
            models.CustomKpi.is_active == 1,
        )
        .order_by(models.CustomKpi.sort_order.asc(), models.CustomKpi.id.asc())
        .all()
    )

    scorecards = report.scorecards or {}
    out: List[Dict[str, Any]] = []
    for k in kpis:
        value, err = evaluate(k.formula, scorecards)
        out.append({
            "id": k.id,
            "name": k.name,
            "formula": k.formula,
            "format": k.format,
            "value": value,
            "error": err,
        })
    return out
