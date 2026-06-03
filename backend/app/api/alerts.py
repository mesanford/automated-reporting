"""Alert-rule CRUD. Mutations are owner/admin only."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user, record_audit
from app.database import get_db
from app.services.security import encrypt_token, decrypt_token

router = APIRouter()


VALID_COMPARISONS = {"gt", "lt", "pct_change_gt"}


# Webhook URLs grant the ability to post to someone's Slack/Chat channel —
# treat them as secrets. We encrypt the `url` (or `to` for email) field
# on write, mask on API read, and decrypt at delivery time in
# `notifications.send_to_channel`.
_SECRET_KEYS = ("url", "to")


def _encrypt_channels(channels: list[dict]) -> list[dict]:
    out = []
    for c in channels:
        copy = dict(c)
        for k in _SECRET_KEYS:
            if copy.get(k):
                copy[k] = encrypt_token(str(copy[k]))
        out.append(copy)
    return out


def _mask_channels_for_display(channels: list[dict] | None) -> list[dict]:
    """Replace stored ciphertext with a redacted hint so the UI can show
    'configured' without leaking the underlying webhook URL."""
    if not channels:
        return []
    out = []
    for c in channels:
        copy = dict(c)
        for k in _SECRET_KEYS:
            if copy.get(k):
                copy[k] = "***"
        out.append(copy)
    return out


def decrypt_channels_for_delivery(channels: list[dict] | None) -> list[dict]:
    """Inverse of `_encrypt_channels`. Used by the alert evaluator."""
    if not channels:
        return []
    out = []
    for c in channels:
        copy = dict(c)
        for k in _SECRET_KEYS:
            v = copy.get(k)
            if v:
                try:
                    copy[k] = decrypt_token(str(v))
                except Exception:
                    # If decryption fails (legacy rows written before this
                    # slice, or key rotation), pass through unchanged so
                    # the channel just won't deliver. Better than crashing
                    # the whole sync.
                    pass
        out.append(copy)
    return out


class AlertCreate(BaseModel):
    name: str
    metric: str
    comparison: str
    threshold: str  # accept as string to keep the wire shape simple
    channels: List[dict] = []
    is_active: bool = True


class AlertUpdate(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    comparison: Optional[str] = None
    threshold: Optional[str] = None
    channels: Optional[List[dict]] = None
    is_active: Optional[bool] = None


def _serialize(a: models.AlertRule) -> dict:
    return {
        "id": a.id,
        "workspace_id": a.workspace_id,
        "name": a.name,
        "metric": a.metric,
        "comparison": a.comparison,
        "threshold": a.threshold,
        "channels": _mask_channels_for_display(a.channels),
        "is_active": bool(a.is_active),
        "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None,
        "last_value": a.last_value,
        "created_at": a.created_at.isoformat() if a.created_at else None,
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


@router.get("/workspaces/{workspace_id}/alerts")
def list_alerts(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin", "member", "viewer"})
    rows = (
        db.query(models.AlertRule)
        .filter(models.AlertRule.workspace_id == workspace_id)
        .order_by(models.AlertRule.created_at.desc())
        .all()
    )
    return [_serialize(a) for a in rows]


@router.post("/workspaces/{workspace_id}/alerts")
def create_alert(
    workspace_id: int,
    body: AlertCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    if body.comparison not in VALID_COMPARISONS:
        raise HTTPException(
            status_code=400,
            detail=f"comparison must be one of {sorted(VALID_COMPARISONS)}.",
        )
    try:
        float(body.threshold)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="threshold must be numeric.")

    rule = models.AlertRule(
        workspace_id=workspace_id,
        name=body.name,
        metric=body.metric,
        comparison=body.comparison,
        threshold=body.threshold,
        channels=_encrypt_channels(body.channels),
        is_active=1 if body.is_active else 0,
        created_by_subject=user_id,
    )
    db.add(rule)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="alert.create",
        target_type="alert_rule",
        target_id=rule.id,
        payload={"name": body.name, "metric": body.metric, "threshold": body.threshold},
    )
    db.commit()
    db.refresh(rule)
    return _serialize(rule)


@router.patch("/workspaces/{workspace_id}/alerts/{alert_id}")
def update_alert(
    workspace_id: int,
    alert_id: int,
    body: AlertUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})

    rule = (
        db.query(models.AlertRule)
        .filter(
            models.AlertRule.id == alert_id,
            models.AlertRule.workspace_id == workspace_id,
        )
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Alert not found.")

    if body.comparison is not None and body.comparison not in VALID_COMPARISONS:
        raise HTTPException(status_code=400, detail="Invalid comparison.")
    if body.threshold is not None:
        try:
            float(body.threshold)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="threshold must be numeric.")

    if body.name is not None: rule.name = body.name
    if body.metric is not None: rule.metric = body.metric
    if body.comparison is not None: rule.comparison = body.comparison
    if body.threshold is not None: rule.threshold = body.threshold
    if body.channels is not None: rule.channels = _encrypt_channels(body.channels)
    if body.is_active is not None: rule.is_active = 1 if body.is_active else 0

    db.commit()
    db.refresh(rule)
    return _serialize(rule)


@router.delete("/workspaces/{workspace_id}/alerts/{alert_id}")
def delete_alert(
    workspace_id: int,
    alert_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_role(db, workspace_id, user_id, {"owner", "admin"})
    rule = (
        db.query(models.AlertRule)
        .filter(
            models.AlertRule.id == alert_id,
            models.AlertRule.workspace_id == workspace_id,
        )
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Alert not found.")
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="alert.delete",
        target_type="alert_rule",
        target_id=str(alert_id),
        payload={"name": rule.name},
    )
    db.delete(rule)
    db.commit()
    return {"status": "deleted", "id": alert_id}
