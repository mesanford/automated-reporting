"""Task handlers registered with the queue.

Importing this module is the act of registration — `main.py` imports it
during startup. Add handlers here so they're discoverable in one place.

Handler functions take a single `payload: dict` and run to completion; the
queue retries on uncaught exception per the Cloud Tasks queue config.

NOTE: The platform-sync flow is still synchronous in `endpoints.sync_connection`
because the frontend awaits the full report payload. Moving it onto this
queue is a coordinated backend + frontend change (frontend polls SyncJob
instead of awaiting the response). The plumbing here is what that future
change will plug into.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from app import models
from app.database import SessionLocal
from app.services.task_queue import register_handler

logger = logging.getLogger(__name__)


@register_handler("sync_connection")
async def sync_connection_handler(payload: Dict[str, Any]) -> None:
    """Run a single-connection platform sync for the given SyncJob id."""
    job_id = payload.get("sync_job_id")
    if not job_id:
        logger.warning("sync_connection: missing sync_job_id in payload")
        return

    from app.services.sync_runner import run_sync_for_job

    await run_sync_for_job(
        int(job_id),
        sync_start_date=payload.get("sync_start_date"),
        sync_end_date=payload.get("sync_end_date"),
        comparison_start_date=payload.get("comparison_start_date"),
        comparison_end_date=payload.get("comparison_end_date"),
    )


@register_handler("sync_all_connections")
async def sync_all_handler(payload: Dict[str, Any]) -> None:
    """Combine every active connection in the workspace into one Report."""
    job_id = payload.get("sync_job_id")
    if not job_id:
        logger.warning("sync_all_connections: missing sync_job_id in payload")
        return

    from app.services.sync_runner import run_sync_all_for_job

    await run_sync_all_for_job(
        int(job_id),
        sync_start_date=payload.get("sync_start_date"),
        sync_end_date=payload.get("sync_end_date"),
        comparison_start_date=payload.get("comparison_start_date"),
        comparison_end_date=payload.get("comparison_end_date"),
    )


@register_handler("touch_sync_job")
async def touch_sync_job(payload: Dict[str, Any]) -> None:
    """Example handler: stamps a SyncJob row with `completed_at = now()`.

    Exists to exercise the enqueue → worker → dispatch path end-to-end in
    dev (and in a Cloud Tasks smoke test) without touching the real sync
    flow. Replace or remove once a real handler lives here.
    """
    job_id = payload.get("sync_job_id")
    if not job_id:
        logger.warning("touch_sync_job: missing sync_job_id in payload")
        return

    db = SessionLocal()
    try:
        job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
        if not job:
            logger.warning("touch_sync_job: SyncJob %s not found", job_id)
            return
        job.completed_at = datetime.utcnow()
        job.status = job.status or "completed"
        db.commit()
        logger.info("touch_sync_job: stamped SyncJob %s", job_id)
    finally:
        db.close()
