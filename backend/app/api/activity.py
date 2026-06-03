"""Workspace activity feed.

Unifies three sources into one chronological timeline:
- `audit_log` — every governance action (invites, role changes, alert
  triggers, budget threshold crossings, optimization approves/executes).
- Recently completed `SyncJob` rows — the periodic "sync finished" beat.
- Recently created `Report` rows — the artifacts those syncs produced.

We do the union on read instead of writing a synthetic feed table — the
underlying rows already exist, double-writing them just creates
consistency risk. Limit is capped at 200 so the timeline stays cheap.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_user
from app.database import get_db

router = APIRouter()


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


@router.get("/workspaces/{workspace_id}/activity")
def list_activity(
    workspace_id: int,
    limit: int = 100,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)
    limit = max(1, min(limit, 200))

    events: List[Dict[str, Any]] = []

    # 1. Audit log — already the richest source.
    audit_rows = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.workspace_id == workspace_id)
        .order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    for a in audit_rows:
        events.append({
            "kind": "audit",
            "id": f"audit:{a.id}",
            "at": a.created_at.isoformat() if a.created_at else None,
            "actor": a.actor_subject,
            "action": a.action,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "payload": a.payload,
        })

    # 2. Sync completions — a heartbeat distinct from any audit entry.
    sync_rows = (
        db.query(models.SyncJob)
        .filter(
            models.SyncJob.workspace_id == workspace_id,
            models.SyncJob.completed_at.isnot(None),
        )
        .order_by(models.SyncJob.completed_at.desc(), models.SyncJob.id.desc())
        .limit(limit)
        .all()
    )
    for s in sync_rows:
        events.append({
            "kind": "sync",
            "id": f"sync:{s.id}",
            "at": s.completed_at.isoformat() if s.completed_at else None,
            "actor": s.user_id,
            "status": s.status,
            "connection_id": s.connection_id,  # NULL for sync-all
            "report_id": s.report_id,
            "error_message": s.error_message,
        })

    # 3. Reports created — the artifact users land on.
    report_rows = (
        db.query(models.Report)
        .filter(models.Report.workspace_id == workspace_id)
        .order_by(models.Report.created_at.desc(), models.Report.id.desc())
        .limit(limit)
        .all()
    )
    for r in report_rows:
        scorecards = r.scorecards or {}
        events.append({
            "kind": "report",
            "id": f"report:{r.id}",
            "at": r.created_at.isoformat() if r.created_at else None,
            "actor": r.user_id,
            "report_id": r.id,
            "period_label": r.current_period_label,
            "total_spend": scorecards.get("totalSpend"),
            "total_conversions": scorecards.get("totalConversions"),
        })

    # Merge + sort descending. Items without timestamps sink to the bottom.
    events.sort(key=lambda e: (e.get("at") or ""), reverse=True)
    return events[:limit]
