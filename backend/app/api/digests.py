"""Digest subscription CRUD + internal scheduler tick."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.api.auth import get_current_identity, get_current_user, record_audit
from app.api.internal import _verify_oidc
from app.database import SessionLocal, get_db
from app.services.digests import compute_next_send, send_due_digests

router = APIRouter()


VALID_CADENCES = {"daily", "weekly"}


class DigestSubscribe(BaseModel):
    cadence: str = "daily"
    email: Optional[str] = None  # falls back to authenticated user's email


def _serialize(s: models.DigestSubscription) -> dict:
    return {
        "id": s.id,
        "workspace_id": s.workspace_id,
        "user_subject": s.user_subject,
        "email": s.email,
        "cadence": s.cadence,
        "is_active": bool(s.is_active),
        "next_send_at": s.next_send_at.isoformat() if s.next_send_at else None,
        "last_sent_at": s.last_sent_at.isoformat() if s.last_sent_at else None,
        "last_send_error": s.last_send_error,
        "created_at": s.created_at.isoformat() if s.created_at else None,
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


@router.get("/workspaces/{workspace_id}/digests/me")
def get_my_subscription(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)
    sub = (
        db.query(models.DigestSubscription)
        .filter(
            models.DigestSubscription.workspace_id == workspace_id,
            models.DigestSubscription.user_subject == user_id,
        )
        .first()
    )
    if not sub:
        return {"subscribed": False}
    return {"subscribed": True, **_serialize(sub)}


@router.post("/workspaces/{workspace_id}/digests/subscribe")
def subscribe(
    workspace_id: int,
    body: DigestSubscribe,
    identity = Depends(get_current_identity),
    db: Session = Depends(get_db),
):
    user_id = identity.uid
    _require_membership(db, workspace_id, user_id)
    if body.cadence not in VALID_CADENCES:
        raise HTTPException(status_code=400, detail=f"cadence must be in {sorted(VALID_CADENCES)}.")
    email = (body.email or identity.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required.")

    sub = (
        db.query(models.DigestSubscription)
        .filter(
            models.DigestSubscription.workspace_id == workspace_id,
            models.DigestSubscription.user_subject == user_id,
        )
        .first()
    )
    if sub:
        sub.email = email
        sub.cadence = body.cadence
        sub.is_active = 1
        sub.next_send_at = compute_next_send(body.cadence)
    else:
        sub = models.DigestSubscription(
            workspace_id=workspace_id,
            user_subject=user_id,
            email=email,
            cadence=body.cadence,
            is_active=1,
            next_send_at=compute_next_send(body.cadence),
        )
        db.add(sub)

    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="digest.subscribe",
        target_type="digest_subscription",
        target_id=str(sub.id),
        payload={"cadence": body.cadence, "email": email},
    )
    db.commit()
    db.refresh(sub)
    return _serialize(sub)


@router.post("/workspaces/{workspace_id}/digests/unsubscribe")
def unsubscribe(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_membership(db, workspace_id, user_id)
    sub = (
        db.query(models.DigestSubscription)
        .filter(
            models.DigestSubscription.workspace_id == workspace_id,
            models.DigestSubscription.user_subject == user_id,
        )
        .first()
    )
    if not sub:
        return {"status": "not_subscribed"}
    sub.is_active = 0
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="digest.unsubscribe",
        target_type="digest_subscription",
        target_id=str(sub.id),
        payload={},
    )
    db.commit()
    return {"status": "unsubscribed"}


@router.post("/internal/digests/tick")
async def digests_tick(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Cloud Scheduler hits this every ~5 minutes. Sends every digest
    whose `next_send_at` is in the past + advances each row.

    OIDC-verified the same way as the sync-scheduler tick."""
    _verify_oidc(authorization)

    db = SessionLocal()
    try:
        sent = send_due_digests(db)
        return {"status": "ok", "sent": sent}
    finally:
        db.close()
