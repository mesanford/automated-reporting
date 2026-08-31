"""Async task queue with a Cloud Tasks backend and an in-process fallback.

Usage:

    from app.services.task_queue import enqueue, register_handler

    @register_handler("sync_connection")
    async def _run_sync(payload: dict) -> None:
        ...

    await enqueue("sync_connection", {"connection_id": 42})

In production (Cloud Tasks): the call schedules an HTTP task that the queue
delivers to the `/api/internal/tasks/run` endpoint on this same Cloud Run
service, signed with an OIDC token (verified in `api/internal.py`). The
worker then dispatches to the registered handler by name.

Locally (no `CLOUD_TASKS_QUEUE` env var): the call runs the handler via
`asyncio.create_task` in-process. Fine for dev, not for production load.

Why a name-based registry instead of pickling functions: Cloud Tasks
delivers HTTP requests, so the worker process needs to look the handler up
by string anyway. Using the same dispatch locally keeps the two paths
behaviorally identical.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

Handler = Callable[[Dict[str, Any]], Awaitable[None]]

_handlers: Dict[str, Handler] = {}


def register_handler(name: str) -> Callable[[Handler], Handler]:
    """Decorator: register an async function as a task handler."""

    def _wrap(fn: Handler) -> Handler:
        if name in _handlers:
            raise ValueError(f"Task handler '{name}' already registered.")
        _handlers[name] = fn
        return fn

    return _wrap


def get_handler(name: str) -> Optional[Handler]:
    return _handlers.get(name)


async def dispatch(name: str, payload: Dict[str, Any]) -> None:
    """Run the registered handler for `name`. Used by the worker endpoint
    and by the in-process fallback."""
    handler = _handlers.get(name)
    if not handler:
        raise ValueError(f"No handler registered for task '{name}'.")
    await handler(payload)


# ── Backend selection ───────────────────────────────────────────────────────

def _cloud_tasks_configured() -> bool:
    return bool(os.getenv("CLOUD_TASKS_QUEUE") and os.getenv("CLOUD_TASKS_WORKER_URL"))


async def enqueue(name: str, payload: Dict[str, Any], *, delay_seconds: int = 0) -> str:
    """Schedule `name(payload)` to run in the background.

    Returns a task identifier (Cloud Tasks task name in prod, asyncio task name locally).
    """
    if _cloud_tasks_configured():
        return await _enqueue_cloud_tasks(name, payload, delay_seconds=delay_seconds)
    return _enqueue_in_process(name, payload, delay_seconds=delay_seconds)


def _enqueue_in_process(name: str, payload: Dict[str, Any], *, delay_seconds: int) -> str:
    async def _run() -> None:
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        try:
            await dispatch(name, payload)
        except Exception:
            logger.exception("In-process task %s failed", name)

    task = asyncio.create_task(_run(), name=f"task:{name}")
    return task.get_name()


async def _enqueue_cloud_tasks(
    name: str, payload: Dict[str, Any], *, delay_seconds: int
) -> str:
    from google.cloud import tasks_v2  # type: ignore
    from google.protobuf import timestamp_pb2  # type: ignore
    import datetime as _dt

    queue_path = os.getenv("CLOUD_TASKS_QUEUE", "")  # full resource path
    worker_url = os.getenv("CLOUD_TASKS_WORKER_URL", "").rstrip("/")
    worker_sa = os.getenv("CLOUD_TASKS_INVOKER_SA", "")
    audience = os.getenv("CLOUD_TASKS_OIDC_AUDIENCE", worker_url)

    if not (queue_path and worker_url and worker_sa):
        raise RuntimeError(
            "Cloud Tasks misconfigured: need CLOUD_TASKS_QUEUE, "
            "CLOUD_TASKS_WORKER_URL, CLOUD_TASKS_INVOKER_SA."
        )

    client = tasks_v2.CloudTasksClient()
    task: Dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{worker_url}/api/internal/tasks/run",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"name": name, "payload": payload}).encode("utf-8"),
            "oidc_token": {
                "service_account_email": worker_sa,
                "audience": audience,
            },
        }
    }

    if delay_seconds:
        ts = timestamp_pb2.Timestamp()
        ts.FromDatetime(_dt.datetime.utcnow() + _dt.timedelta(seconds=delay_seconds))
        task["schedule_time"] = ts

    response = client.create_task(request={"parent": queue_path, "task": task})
    return response.name
