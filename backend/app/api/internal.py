"""Internal endpoints called by Cloud Tasks (and similar GCP callers).

`/api/internal/tasks/run` receives task deliveries from Cloud Tasks. The
queue signs each delivery with an OIDC token whose audience is this
service; we verify the token and dispatch to the registered handler.

The endpoint is also reachable from in-process tests via the
`ALLOW_INTERNAL_NO_OIDC=1` escape hatch (local dev). Never enable that in
production — it removes the only thing preventing anyone on the internet
from invoking arbitrary task handlers.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.services import task_queue

logger = logging.getLogger(__name__)
router = APIRouter()


class TaskEnvelope(BaseModel):
    name: str
    payload: Dict[str, Any] = {}


def _expected_audience() -> str:
    return os.getenv("CLOUD_TASKS_OIDC_AUDIENCE", "").strip()


def _expected_issuer_email() -> str:
    """Service account that Cloud Tasks signs with — must match the value
    configured on the queue (`CLOUD_TASKS_INVOKER_SA`)."""
    return os.getenv("CLOUD_TASKS_INVOKER_SA", "").strip()


def _verify_oidc(authorization: Optional[str]) -> None:
    """Verify the Cloud Tasks OIDC token. Raises 401 on any failure."""
    if os.getenv("ALLOW_INTERNAL_NO_OIDC", "0") == "1":
        return

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")

    token = authorization.split(" ", 1)[1].strip()
    audience = _expected_audience()
    expected_email = _expected_issuer_email()

    if not audience or not expected_email:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal worker not configured (CLOUD_TASKS_OIDC_AUDIENCE / CLOUD_TASKS_INVOKER_SA).",
        )

    try:
        from google.auth.transport import requests as g_requests  # type: ignore
        from google.oauth2 import id_token  # type: ignore

        claims = id_token.verify_oauth2_token(token, g_requests.Request(), audience=audience)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"OIDC verify failed: {exc}")

    email = claims.get("email", "")
    if email != expected_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unexpected OIDC issuer ({email}).",
        )


@router.post("/tasks/run")
async def run_task(
    envelope: TaskEnvelope,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    _verify_oidc(authorization)

    handler = task_queue.get_handler(envelope.name)
    if not handler:
        # 200 instead of 404 so Cloud Tasks does not retry forever for an
        # unknown handler name (the misconfiguration is on our side).
        logger.error("Cloud Tasks delivered unknown task: %s", envelope.name)
        return {"status": "no_handler", "name": envelope.name}

    try:
        await task_queue.dispatch(envelope.name, envelope.payload)
    except Exception as exc:  # noqa: BLE001
        # 5xx → Cloud Tasks retries per the queue retry config.
        logger.exception("Task %s failed", envelope.name)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return {"status": "ok", "name": envelope.name}
