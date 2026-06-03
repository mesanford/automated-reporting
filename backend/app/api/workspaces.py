"""Workspace management API.

For now: list the workspaces the signed-in user belongs to, create a new
one, list members, and skeleton invite-by-email endpoints. The invite
token-send flow is left as a TODO until an email provider is wired up
(GCP-side: Cloud Functions + SendGrid is the usual answer).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.services.email_service import send_invite_email
from app.api.auth import (
    get_current_identity,
    get_current_user,
    Identity,
    record_audit,
    require_role,
)
from app.database import get_db

router = APIRouter()


# ── Pydantic models ─────────────────────────────────────────────────────────


class WorkspaceCreate(BaseModel):
    name: str
    slug: Optional[str] = None  # auto-generated if absent


class InviteCreate(BaseModel):
    email: str
    role: str = "member"


class RoleUpdate(BaseModel):
    role: str  # member | admin | viewer (owners promoted via separate flow)


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    base_currency: Optional[str] = None


def _slugify(name: str) -> str:
    base = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    return base or "workspace"


def _serialize_workspace(w: models.Workspace, role: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": w.id,
        "name": w.name,
        "slug": w.slug,
        "role": role,
        "base_currency": getattr(w, "base_currency", None) or "USD",
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


def _membership_for(db: Session, workspace_id: int, user_id: str) -> Optional[models.Membership]:
    return (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )


def _require_role(
    db: Session, workspace_id: int, user_id: str, allowed: List[str]
) -> models.Membership:
    membership = _membership_for(db, workspace_id, user_id)
    if not membership or membership.role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient role for this action.")
    return membership


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("")
def list_my_workspaces(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List every workspace the user belongs to, with their role."""
    rows = (
        db.query(models.Membership, models.Workspace)
        .join(models.Workspace, models.Membership.workspace_id == models.Workspace.id)
        .filter(models.Membership.user_subject == user_id)
        .order_by(models.Workspace.created_at.asc())
        .all()
    )
    return [_serialize_workspace(w, role=m.role) for m, w in rows]


@router.post("")
def create_workspace(
    body: WorkspaceCreate,
    identity: Identity = Depends(get_current_identity),
    db: Session = Depends(get_db),
):
    slug_base = body.slug or _slugify(body.name)
    slug = slug_base
    suffix = 1
    # Resolve slug collisions by appending -2, -3, ...
    while db.query(models.Workspace).filter(models.Workspace.slug == slug).first():
        suffix += 1
        slug = f"{slug_base}-{suffix}"

    ws = models.Workspace(name=body.name, slug=slug, created_by_subject=identity.uid)
    db.add(ws)
    db.flush()
    db.add(models.Membership(workspace_id=ws.id, user_subject=identity.uid, role="owner"))

    # Ensure a User row exists so the workspace creator shows up in the UI.
    if not db.query(models.User).filter(models.User.auth_subject == identity.uid).first():
        db.add(models.User(auth_subject=identity.uid, email=identity.email, name=identity.name))

    db.commit()
    db.refresh(ws)
    return _serialize_workspace(ws, role="owner")


@router.patch("/{workspace_id}")
def update_workspace(
    workspace_id: int,
    body: WorkspaceUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Owner/admin: rename or change base currency. Currency only affects
    display + the conversion path in `services/fx.convert`; existing
    Reports remain stamped with the value at the time they were created."""
    _require_role(db, workspace_id, user_id, ["owner", "admin"])
    ws = db.query(models.Workspace).filter(models.Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if body.name is not None:
        ws.name = body.name
    if body.base_currency is not None:
        ccy = body.base_currency.strip().upper()
        if len(ccy) != 3 or not ccy.isalpha():
            raise HTTPException(status_code=400, detail="base_currency must be a 3-letter ISO code.")
        ws.base_currency = ccy
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="workspace.update",
        target_type="workspace",
        target_id=workspace_id,
        payload={"name": ws.name, "base_currency": ws.base_currency},
    )
    db.commit()
    db.refresh(ws)
    # The caller's membership role for serialization.
    m = _membership_for(db, workspace_id, user_id)
    return _serialize_workspace(ws, role=m.role if m else None)


@router.get("/{workspace_id}/members")
def list_members(
    workspace_id: int,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Any member can see the roster.
    if not _membership_for(db, workspace_id, user_id):
        raise HTTPException(status_code=403, detail="Not a member of this workspace.")

    rows = (
        db.query(models.Membership, models.User)
        .outerjoin(models.User, models.User.auth_subject == models.Membership.user_subject)
        .filter(models.Membership.workspace_id == workspace_id)
        .all()
    )
    return [
        {
            "user_subject": m.user_subject,
            "role": m.role,
            "email": (u.email if u else None),
            "name": (u.name if u else None),
            "joined_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m, u in rows
    ]


@router.post("/{workspace_id}/invites")
def create_invite(
    workspace_id: int,
    body: InviteCreate,
    request: Request,
    identity = Depends(get_current_identity),
    db: Session = Depends(get_db),
):
    """Mint an invite token and try to email it. The response always
    includes `token` and `accept_url` as a backup the UI can offer when
    delivery fails or no email provider is configured."""
    user_id = identity.uid
    _require_role(db, workspace_id, user_id, ["owner", "admin"])
    if body.role not in {"admin", "member", "viewer"}:
        raise HTTPException(status_code=400, detail="Invalid role.")

    workspace = db.query(models.Workspace).filter(models.Workspace.id == workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.utcnow() + timedelta(days=7)

    invite = models.Invite(
        workspace_id=workspace_id,
        email=body.email.strip().lower(),
        role=body.role,
        token_hash=token_hash,
        invited_by_subject=user_id,
        expires_at=expires_at,
    )
    db.add(invite)
    db.flush()
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="invite.create",
        target_type="invite",
        target_id=invite.id,
        payload={"email": invite.email, "role": invite.role},
    )
    db.commit()
    db.refresh(invite)

    # Build the accept URL using the same FRONTEND_URL the rest of the app uses.
    frontend_url = (
        os.getenv("FRONTEND_URL")
        or str(request.base_url).rstrip("/").replace(":8000", ":3000")
    )
    accept_url = f"{frontend_url.rstrip('/')}/invites/accept?token={token}"

    delivery = send_invite_email(
        to_email=invite.email,
        accept_url=accept_url,
        workspace_name=workspace.name,
        role=invite.role,
        expires_at=expires_at,
        invited_by=identity.email or identity.name or identity.uid,
    )

    return {
        "invite_id": invite.id,
        "email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
        "token": token,
        "accept_url": accept_url,
        "email_delivery": delivery,
    }


@router.patch("/{workspace_id}/members/{user_subject}")
def update_member_role(
    workspace_id: int,
    user_subject: str,
    body: RoleUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change a member's role. Owner-only. Owners can't be demoted via this
    endpoint — protects against the last owner accidentally locking everyone
    out. Owner reassignment will need a dedicated flow."""
    _require_role(db, workspace_id, user_id, ["owner"])

    if body.role not in {"admin", "member", "viewer"}:
        raise HTTPException(status_code=400, detail="Invalid role.")

    target = _membership_for(db, workspace_id, user_subject)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found.")
    if target.role == "owner":
        raise HTTPException(
            status_code=400,
            detail="Cannot change an owner's role via this endpoint.",
        )

    old_role = target.role
    target.role = body.role
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="member.role_change",
        target_type="membership",
        target_id=target.id,
        payload={"user_subject": user_subject, "from": old_role, "to": body.role},
    )
    db.commit()
    return {"status": "ok", "user_subject": user_subject, "role": body.role}


@router.delete("/{workspace_id}/members/{user_subject}")
def remove_member(
    workspace_id: int,
    user_subject: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a member. Owner-only. Refuses to remove the last owner."""
    _require_role(db, workspace_id, user_id, ["owner"])

    target = _membership_for(db, workspace_id, user_subject)
    if not target:
        raise HTTPException(status_code=404, detail="Member not found.")

    if target.role == "owner":
        owner_count = (
            db.query(models.Membership)
            .filter(
                models.Membership.workspace_id == workspace_id,
                models.Membership.role == "owner",
            )
            .count()
        )
        if owner_count <= 1:
            raise HTTPException(
                status_code=400, detail="Cannot remove the last owner."
            )

    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="member.remove",
        target_type="membership",
        target_id=target.id,
        payload={"user_subject": user_subject, "role": target.role},
    )
    db.delete(target)
    db.commit()
    return {"status": "removed", "user_subject": user_subject}


@router.get("/{workspace_id}/audit-log")
def list_audit_log(
    workspace_id: int,
    limit: int = 100,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Audit log for the workspace. Owner/admin only."""
    _require_role(db, workspace_id, user_id, ["owner", "admin"])

    limit = max(1, min(limit, 500))
    rows = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.workspace_id == workspace_id)
        .order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "actor_subject": r.actor_subject,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "payload": r.payload,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/invites/accept")
def accept_invite(
    token: str,
    identity: Identity = Depends(get_current_identity),
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    invite = (
        db.query(models.Invite)
        .filter(
            models.Invite.token_hash == token_hash,
            models.Invite.accepted_at.is_(None),
        )
        .first()
    )
    if not invite or invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired invite.")

    # Optionally enforce that the invite's email matches the user's email.
    # Skip strict enforcement for now — Firebase email may be unverified.

    if not _membership_for(db, invite.workspace_id, identity.uid):
        db.add(models.Membership(
            workspace_id=invite.workspace_id,
            user_subject=identity.uid,
            role=invite.role,
        ))

    invite.accepted_at = datetime.utcnow()

    if not db.query(models.User).filter(models.User.auth_subject == identity.uid).first():
        db.add(models.User(auth_subject=identity.uid, email=identity.email, name=identity.name))

    record_audit(
        db,
        workspace_id=invite.workspace_id,
        actor_subject=identity.uid,
        action="invite.accept",
        target_type="invite",
        target_id=invite.id,
        payload={"role": invite.role},
    )
    db.commit()
    return {
        "status": "accepted",
        "workspace_id": invite.workspace_id,
        "role": invite.role,
    }
