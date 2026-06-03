"""Scheduled-sync CRUD + the internal scheduler tick.

Mutations require owner/admin role; listing is open to any workspace
member. The `/tick` endpoint is OIDC-verified through the same path
Cloud Tasks uses (`internal.py` shares the verifier).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import (
    get_current_user,
    record_audit,
    require_role,
)
from app.api.internal import _verify_oidc
from app.database import SessionLocal, get_db
from app.services.scheduling import compute_next_run_at, validate_schedule_inputs

router = APIRouter()


class ScheduleCreate(BaseModel):
    name: str
    frequency: str  # daily | weekly
    hour_utc: int
    day_of_week: Optional[int] = None
    is_active: bool = True


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    frequency: Optional[str] = None
    hour_utc: Optional[int] = None
    day_of_week: Optional[int] = None
    is_active: Optional[bool] = None


def _serialize(s: models.ScheduledSync) -> dict:
    return {
        "id": s.id,
        "workspace_id": s.workspace_id,
        "name": s.name,
        "frequency": s.frequency,
        "hour_utc": s.hour_utc,
        "day_of_week": s.day_of_week,
        "is_active": bool(s.is_active),
        "created_by_subject": s.created_by_subject,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
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


# ── CRUD ────────────────────────────────────────────────────────────────────


@router.get("/workspaces/{workspace_id}/schedules")
def list_schedules(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)
    rows = (
        db.query(models.ScheduledSync)
        .filter(models.ScheduledSync.workspace_id == workspace_id)
        .order_by(models.ScheduledSync.created_at.desc())
        .all()
    )
    return [_serialize(s) for s in rows]


@router.post("/workspaces/{workspace_id}/schedules")
def create_schedule(
    workspace_id: int,
    body: ScheduleCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # The role gate fires before we touch the row.
    _require_membership(db, workspace_id, user_id)
    # We can't compose require_role here easily without restructuring the dep
    # tree, so inline-check.
    m = (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )
    if m.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Requires owner or admin role.")

    err = validate_schedule_inputs(
        frequency=body.frequency, hour_utc=body.hour_utc, day_of_week=body.day_of_week
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    next_run = compute_next_run_at(
        frequency=body.frequency,
        hour_utc=body.hour_utc,
        day_of_week=body.day_of_week,
    )

    sched = models.ScheduledSync(
        workspace_id=workspace_id,
        name=body.name,
        frequency=body.frequency,
        hour_utc=body.hour_utc,
        day_of_week=body.day_of_week if body.frequency == "weekly" else None,
        is_active=1 if body.is_active else 0,
        created_by_subject=user_id,
        next_run_at=next_run,
    )
    db.add(sched)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="schedule.create",
        target_type="scheduled_sync",
        target_id=sched.id,
        payload={"name": body.name, "frequency": body.frequency, "hour_utc": body.hour_utc},
    )
    db.commit()
    db.refresh(sched)
    return _serialize(sched)


@router.patch("/workspaces/{workspace_id}/schedules/{schedule_id}")
def update_schedule(
    workspace_id: int,
    schedule_id: int,
    body: ScheduleUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )
    if not m or m.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Requires owner or admin role.")

    sched = (
        db.query(models.ScheduledSync)
        .filter(
            models.ScheduledSync.id == schedule_id,
            models.ScheduledSync.workspace_id == workspace_id,
        )
        .first()
    )
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    new_freq = body.frequency or sched.frequency
    new_hour = body.hour_utc if body.hour_utc is not None else sched.hour_utc
    new_dow = body.day_of_week if body.day_of_week is not None else sched.day_of_week
    err = validate_schedule_inputs(
        frequency=new_freq, hour_utc=new_hour, day_of_week=new_dow
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    changed_cadence = (
        new_freq != sched.frequency
        or new_hour != sched.hour_utc
        or new_dow != sched.day_of_week
    )

    if body.name is not None:
        sched.name = body.name
    sched.frequency = new_freq
    sched.hour_utc = new_hour
    sched.day_of_week = new_dow if new_freq == "weekly" else None
    if body.is_active is not None:
        sched.is_active = 1 if body.is_active else 0
    if changed_cadence:
        sched.next_run_at = compute_next_run_at(
            frequency=sched.frequency,
            hour_utc=sched.hour_utc,
            day_of_week=sched.day_of_week,
        )

    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="schedule.update",
        target_type="scheduled_sync",
        target_id=sched.id,
        payload={"is_active": bool(sched.is_active), "name": sched.name},
    )
    db.commit()
    db.refresh(sched)
    return _serialize(sched)


@router.delete("/workspaces/{workspace_id}/schedules/{schedule_id}")
def delete_schedule(
    workspace_id: int,
    schedule_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    m = (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )
    if not m or m.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Requires owner or admin role.")

    sched = (
        db.query(models.ScheduledSync)
        .filter(
            models.ScheduledSync.id == schedule_id,
            models.ScheduledSync.workspace_id == workspace_id,
        )
        .first()
    )
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="schedule.delete",
        target_type="scheduled_sync",
        target_id=sched.id,
        payload={"name": sched.name},
    )
    db.delete(sched)
    db.commit()
    return {"status": "deleted", "id": schedule_id}


# ── Scheduler tick (OIDC) ──────────────────────────────────────────────────


@router.post("/internal/scheduler/tick")
async def scheduler_tick(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Cloud Scheduler hits this every ~5 minutes. Picks up every
    ScheduledSync that is due, enqueues a sync-all task for it, and
    advances `next_run_at`. Idempotent: re-running the tick for the same
    timeframe is safe (we only enqueue rows that haven't been advanced)."""
    _verify_oidc(authorization)

    from app.services.task_queue import enqueue

    db = SessionLocal()
    enqueued = []
    try:
        now = datetime.utcnow()
        due = (
            db.query(models.ScheduledSync)
            .filter(
                models.ScheduledSync.is_active == 1,
                models.ScheduledSync.next_run_at <= now,
            )
            .all()
        )

        for sched in due:
            job = models.SyncJob(
                workspace_id=sched.workspace_id,
                user_id=sched.created_by_subject or "scheduler",
                connection_id=None,
                status="pending",
                current_step="scheduled",
            )
            db.add(job)
            db.flush()

            task_id = await enqueue(
                "sync_all_connections",
                {"sync_job_id": job.id},
            )

            sched.last_run_at = now
            sched.next_run_at = compute_next_run_at(
                frequency=sched.frequency,
                hour_utc=sched.hour_utc,
                day_of_week=sched.day_of_week,
                now=now,
            )
            enqueued.append({
                "schedule_id": sched.id,
                "workspace_id": sched.workspace_id,
                "sync_job_id": job.id,
                "task_id": task_id,
            })

        db.commit()
    finally:
        db.close()

    return {"status": "ok", "enqueued": enqueued}
