"""Transactional email with provider-agnostic backends.

Resolution order:
1. `SENDGRID_API_KEY` set → SendGrid backend.
2. Otherwise → no-op backend that logs the rendered message body.

The no-op path keeps local development friction-free: the settings UI
still surfaces the copy-link panel from the invite response so the owner
can hand-deliver the link. Once `SENDGRID_API_KEY` and `INVITE_FROM_EMAIL`
are configured, the same code path delivers via SendGrid and the response
flags `delivered: true`.

API:
    send_invite_email(
        to_email, accept_url, workspace_name, role, expires_at, invited_by,
    ) -> dict with keys: delivered (bool), provider (str), error (str|None)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from app.services.secrets_manager import get_secret

logger = logging.getLogger(__name__)


def _from_address() -> str:
    return (
        get_secret("INVITE_FROM_EMAIL")
        or os.getenv("INVITE_FROM_EMAIL")
        or "no-reply@antigravity.local"
    )


def _from_name() -> str:
    return os.getenv("INVITE_FROM_NAME") or "Antigravity"


def _render_invite_body(
    accept_url: str,
    workspace_name: str,
    role: str,
    expires_at: datetime,
    invited_by: Optional[str],
) -> tuple[str, str]:
    """Return (subject, plain-text body). Kept dependency-free."""
    subject = f"You're invited to {workspace_name} on Antigravity"
    inviter_line = f"{invited_by} invited you" if invited_by else "You've been invited"
    expires = expires_at.strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"{inviter_line} to join {workspace_name} on Antigravity as {role}.\n\n"
        f"Accept the invite:\n{accept_url}\n\n"
        f"This link expires {expires} and can only be used once.\n\n"
        f"— Antigravity"
    )
    return subject, body


def send_invite_email(
    *,
    to_email: str,
    accept_url: str,
    workspace_name: str,
    role: str,
    expires_at: datetime,
    invited_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Render + deliver an invite email via the configured backend."""
    subject, body = _render_invite_body(
        accept_url, workspace_name, role, expires_at, invited_by
    )

    api_key = get_secret("SENDGRID_API_KEY")
    if api_key:
        return _send_sendgrid(api_key, to_email, subject, body)

    # No-op: log so dev/test can verify the call shape; UI falls back to
    # the copy-link panel.
    logger.info(
        "email_service[noop] to=%s subject=%s body=%s",
        to_email, subject, body.replace("\n", " ⏎ "),
    )
    return {"delivered": False, "provider": "noop", "error": None}


def _send_sendgrid(
    api_key: str, to_email: str, subject: str, body: str
) -> Dict[str, Any]:
    """POST to SendGrid v3 directly so we don't take on the sendgrid SDK
    just for one HTTP call. Returns the standard envelope."""
    try:
        import httpx  # already a backend dep

        response = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": _from_address(), "name": _from_name()},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            },
            timeout=10.0,
        )
        if response.status_code >= 300:
            return {
                "delivered": False,
                "provider": "sendgrid",
                "error": f"SendGrid returned {response.status_code}: {response.text[:200]}",
            }
        return {"delivered": True, "provider": "sendgrid", "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"delivered": False, "provider": "sendgrid", "error": str(exc)}
