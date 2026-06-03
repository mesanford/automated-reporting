"""Async sync flow: enqueue creates a SyncJob, in-process queue runs the
handler, status endpoint reflects progress.

Connectors are stubbed at module level in conftest, so we patch the sync
runner instead of trying to actually fetch from real ad platforms.
"""
import asyncio

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def seeded_connection(client, db, alice):
    """Make Alice's Personal workspace exist and seed a Connection."""
    # Touch any workspace-scoped endpoint to trigger auto-provisioning.
    client.get("/api/chat/conversations", headers=auth_headers(alice))

    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    conn = models.Connection(
        workspace_id=ws.id,
        user_id=alice,
        platform="google",
        account_id="g-1",
        account_name="Test Account",
        access_token="",
        refresh_token="",
        is_active=1,
        selected_account_ids=["g-1"],
        available_accounts=[{"id": "g-1", "name": "Test Account"}],
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn.id, ws.id


def test_enqueue_returns_job_id_immediately(client, seeded_connection, alice):
    conn_id, ws_id = seeded_connection
    r = client.post(
        f"/api/sync/{conn_id}/enqueue",
        json={},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "enqueued"
    assert isinstance(body["sync_job_id"], int)
    assert "task_id" in body


def test_enqueue_writes_sync_job_row(client, db, seeded_connection, alice):
    conn_id, ws_id = seeded_connection
    body = client.post(
        f"/api/sync/{conn_id}/enqueue",
        json={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        headers=auth_headers(alice, ws_id),
    ).json()

    from app import models

    job = db.query(models.SyncJob).filter(models.SyncJob.id == body["sync_job_id"]).first()
    assert job is not None
    assert job.workspace_id == ws_id
    assert job.connection_id == conn_id
    assert job.user_id == alice
    # We don't assert on status because the in-process queue may have
    # already started or finished the task between enqueue and read. The
    # row-creation invariant is what the API guarantees.


def test_enqueue_rejects_non_workspace_connection(client, seeded_connection, alice, bob):
    """Bob has no membership in Alice's workspace — connection must 404 for him."""
    conn_id, _ws_id = seeded_connection
    r = client.post(
        f"/api/sync/{conn_id}/enqueue",
        json={},
        headers=auth_headers(bob),
    )
    # Bob's auto-provisioned Personal workspace doesn't contain that connection.
    assert r.json() == {"status": "error", "message": "Connection not found"}


def test_handler_runs_sync_and_marks_job_completed(db, seeded_connection, alice, monkeypatch):
    """Unit-test `run_sync_for_job` directly: given a SyncJob row, it
    flips it through `running` → `completed` and stamps the report_id.

    Bypasses the in-process asyncio queue, since TestClient tears down its
    event loop between requests and the queue's fire-and-forget task can
    be orphaned mid-test. The queue plumbing itself is verified by the
    `touch_sync_job` example handler in dev."""
    conn_id, ws_id = seeded_connection

    from app import models
    from app.services import sync_runner

    # Seed a SyncJob the same shape the API endpoint would create.
    job = models.SyncJob(
        workspace_id=ws_id,
        user_id=alice,
        connection_id=conn_id,
        status="pending",
        progress_percent=0,
        current_step="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    captured: dict = {}

    async def fake_run_sync(**kwargs):
        captured.update(kwargs)
        return {"id": 12345}

    monkeypatch.setattr(sync_runner, "run_sync", fake_run_sync)

    asyncio.run(sync_runner.run_sync_for_job(
        job_id,
        sync_start_date="2026-01-01",
        sync_end_date="2026-01-31",
    ))

    db.expire_all()
    job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
    assert job.status == "completed"
    assert job.report_id == 12345
    assert job.progress_percent == 100
    assert job.current_step == "done"
    assert captured["workspace_id"] == ws_id
    assert captured["sync_start_date"] == "2026-01-01"


def test_handler_marks_job_failed_on_sync_error(db, seeded_connection, alice, monkeypatch):
    """A SyncError raised by run_sync must land the job in `failed` with
    the human-facing message, not retry-bait the queue."""
    conn_id, ws_id = seeded_connection

    from app import models
    from app.services import sync_runner

    job = models.SyncJob(
        workspace_id=ws_id,
        user_id=alice,
        connection_id=conn_id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    async def boom(**kwargs):
        raise sync_runner.SyncError("Connection not found")

    monkeypatch.setattr(sync_runner, "run_sync", boom)

    asyncio.run(sync_runner.run_sync_for_job(job_id))

    db.expire_all()
    job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
    assert job.status == "failed"
    assert "Connection not found" in (job.error_message or "")
