"""Authentication + workspace-scoping dependencies.

Verifies Firebase ID tokens passed as `Authorization: Bearer <token>`. Falls
back to the legacy `X-User-Id` header path when `ALLOW_DEV_AUTH=1`, so local
development without Firebase credentials still works.

The dependency returns the Firebase UID as a plain string to keep call-site
signatures stable across the codebase (`user_id: str = Depends(get_current_user)`).
Richer identity (email, name) is available via `get_current_identity`.

`get_current_workspace_id(user_id, x_workspace_id, db)` resolves the active
workspace for the request. If the client sent `X-Workspace-Id`, membership
is verified. Otherwise we fall back to the user's owned Personal workspace
(auto-provisioned on first call), so existing endpoints keep working
without forcing the frontend to send the header.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

_firebase_ready = False
_firebase_import_error: Optional[str] = None


def _ensure_firebase() -> None:
    """Initialize firebase_admin once; cache success/failure."""
    global _firebase_ready, _firebase_import_error
    if _firebase_ready or _firebase_import_error:
        return
    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:  # noqa: SLF001
            # Uses GOOGLE_APPLICATION_CREDENTIALS automatically if set.
            # FIREBASE_PROJECT_ID is optional; only needed when ADC can't infer it.
            project_id = os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GCLOUD_PROJECT")
            options = {"projectId": project_id} if project_id else None
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, options)
        _firebase_ready = True
    except Exception as exc:  # noqa: BLE001
        _firebase_import_error = str(exc)


def _allow_dev_auth() -> bool:
    return os.getenv("ALLOW_DEV_AUTH", "0") == "1"


@dataclass
class Identity:
    uid: str
    email: Optional[str] = None
    name: Optional[str] = None


async def get_current_identity(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
) -> Identity:
    # 1. Try Firebase ID token (production path)
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        _ensure_firebase()
        if _firebase_ready:
            try:
                from firebase_admin import auth as firebase_auth

                decoded = firebase_auth.verify_id_token(token)
                return Identity(
                    uid=decoded["uid"],
                    email=decoded.get("email"),
                    name=decoded.get("name"),
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid Firebase ID token: {exc}",
                )
        # Bearer token present but Firebase isn't configured — refuse rather
        # than silently fall back, otherwise an attacker could downgrade auth.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Firebase Admin not initialized ({_firebase_import_error}). "
                "Set GOOGLE_APPLICATION_CREDENTIALS or run with ALLOW_DEV_AUTH=1."
            ),
        )

    # 2. Dev fallback — only when explicitly enabled
    if _allow_dev_auth():
        return Identity(uid=x_user_id or "dev_user_123")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid Authorization header.",
    )


async def get_current_user(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None),
) -> str:
    """Backwards-compatible dependency returning just the UID string."""
    identity = await get_current_identity(authorization=authorization, x_user_id=x_user_id)
    return identity.uid


def _ensure_personal_workspace(db: Session, user_id: str) -> int:
    """Auto-provision a Personal workspace + owner membership for a user
    that doesn't have one yet. Idempotent. Returns workspace_id."""
    from app import models

    membership = (
        db.query(models.Membership)
        .filter(models.Membership.user_subject == user_id)
        .order_by(models.Membership.id.asc())
        .first()
    )
    if membership:
        return membership.workspace_id

    # No memberships → create Personal workspace + owner row.
    slug = f"personal-{user_id}"
    workspace = (
        db.query(models.Workspace).filter(models.Workspace.slug == slug).first()
    )
    if workspace is None:
        workspace = models.Workspace(
            name="Personal", slug=slug, created_by_subject=user_id
        )
        db.add(workspace)
        db.flush()

    db.add(models.Membership(
        workspace_id=workspace.id, user_subject=user_id, role="owner"
    ))

    # Also ensure a Users row exists so future workspace-management UI can
    # show emails/names without writing to memberships first.
    user_row = (
        db.query(models.User).filter(models.User.auth_subject == user_id).first()
    )
    if user_row is None:
        db.add(models.User(auth_subject=user_id))

    db.commit()
    return workspace.id


ROLES_ORDER = ["viewer", "member", "admin", "owner"]


def _membership(db: "Session", workspace_id: int, user_id: str):
    from app import models

    return (
        db.query(models.Membership)
        .filter(
            models.Membership.workspace_id == workspace_id,
            models.Membership.user_subject == user_id,
        )
        .first()
    )


def require_role(allowed: list[str]):
    """Dependency factory: 403 unless the caller's membership role is in
    `allowed`. Composes after `get_current_workspace_id`. Returns the role
    string so handlers can branch on it if useful."""

    async def _dep(
        user_id: str = Depends(get_current_user),
        workspace_id: int = Depends(get_current_workspace_id),
    ) -> str:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            m = _membership(db, workspace_id, user_id)
            if not m or m.role not in allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Requires role in {allowed} (you are {m.role if m else 'not a member'}).",
                )
            return m.role
        finally:
            db.close()

    return _dep


def record_audit(
    db: "Session",
    *,
    workspace_id: int,
    actor_subject: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """Write one audit entry. Caller is responsible for db.commit()."""
    from app import models

    db.add(models.AuditLog(
        workspace_id=workspace_id,
        actor_subject=actor_subject,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        payload=payload,
    ))


async def get_current_workspace_id(
    x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
    user_id: str = Depends(get_current_user),
) -> int:
    """Resolve the active workspace_id for this request.

    - If the client sent `X-Workspace-Id`, validate membership and use it.
    - Otherwise, auto-provision (or look up) the user's Personal workspace.
    """
    from app.database import SessionLocal

    db: Session = SessionLocal()
    try:
        if x_workspace_id:
            try:
                wid = int(x_workspace_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid X-Workspace-Id.")
            from app import models

            membership = (
                db.query(models.Membership)
                .filter(
                    models.Membership.workspace_id == wid,
                    models.Membership.user_subject == user_id,
                )
                .first()
            )
            if not membership:
                raise HTTPException(
                    status_code=403, detail="Not a member of this workspace."
                )
            return wid

        return _ensure_personal_workspace(db, user_id)
    finally:
        db.close()
