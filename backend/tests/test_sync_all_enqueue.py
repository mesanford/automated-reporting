"""Async sync-all: enqueue creates a SyncJob with connection_id=NULL,
the handler runs run_sync_all and lands the combined report on the job."""
import asyncio

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def alice_with_connections(client, db, alice):
    """Alice's workspace plus two active connections to fan out across."""
    client.get("/api/chat/conversations", headers=auth_headers(alice))
    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    for platform, account_id in (("google", "g-1"), ("meta", "m-1")):
        db.add(models.Connection(
            workspace_id=ws.id,
            user_id=alice,
            platform=platform,
            account_id=account_id,
            account_name=f"{platform} test",
            access_token="",
            refresh_token="",
            is_active=1,
            selected_account_ids=[account_id],
            available_accounts=[{"id": account_id, "name": f"{platform} test"}],
        ))
    db.commit()
    return ws.id


def test_enqueue_sync_all_returns_job_id(client, alice_with_connections, alice):
    ws_id = alice_with_connections
    r = client.post(
        "/api/sync-all/enqueue",
        json={},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "enqueued"
    assert isinstance(body["sync_job_id"], int)


def test_enqueue_sync_all_writes_job_with_null_connection_id(
    client, db, alice_with_connections, alice
):
    """The 'all' flavor signals itself via connection_id=NULL on the job row."""
    ws_id = alice_with_connections
    body = client.post(
        "/api/sync-all/enqueue", json={}, headers=auth_headers(alice, ws_id)
    ).json()

    from app import models

    # expire_all so we read fresh row state — the in-process queue handler
    # may have flipped status to `failed` (it tries to run real connectors
    # against stubbed SDKs), but the workspace/connection/total fields
    # written by the endpoint don't change.
    db.expire_all()
    job = db.query(models.SyncJob).filter(models.SyncJob.id == body["sync_job_id"]).first()
    assert job is not None
    assert job.connection_id is None
    assert job.workspace_id == ws_id
    assert job.total_accounts == 2  # matches the two seeded active connections


def test_enqueue_sync_all_404s_when_no_active_connections(client, alice):
    """Alice has no connections in her auto-provisioned Personal workspace."""
    # Trigger workspace creation but seed no connections.
    client.get("/api/chat/conversations", headers=auth_headers(alice))
    r = client.post(
        "/api/sync-all/enqueue", json={}, headers=auth_headers(alice)
    )
    body = r.json()
    assert body == {"status": "error", "message": "No active connections found."}


def test_sync_all_handler_marks_job_completed(db, alice_with_connections, alice, monkeypatch):
    """Drive the task handler directly: it must flip the job to `completed`
    and stamp the report_id, just like the single-connection handler does."""
    ws_id = alice_with_connections

    from app import models
    from app.services import sync_runner

    job = models.SyncJob(
        workspace_id=ws_id,
        user_id=alice,
        connection_id=None,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id

    captured = {}

    async def fake_run_sync_all(**kwargs):
        captured.update(kwargs)
        return {"id": 77777}

    monkeypatch.setattr(sync_runner, "run_sync_all", fake_run_sync_all)

    asyncio.run(sync_runner.run_sync_all_for_job(
        job_id,
        sync_start_date="2026-02-01",
        sync_end_date="2026-02-28",
    ))

    db.expire_all()
    job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
    assert job.status == "completed"
    assert job.report_id == 77777
    assert job.progress_percent == 100
    assert captured["workspace_id"] == ws_id
    assert captured["sync_start_date"] == "2026-02-01"


def test_sync_all_isolation_across_workspaces(client, alice_with_connections, bob):
    """Bob hits enqueue without a workspace header → he gets his own (empty)
    Personal workspace, not Alice's. So he sees 'No active connections'."""
    r = client.post(
        "/api/sync-all/enqueue", json={}, headers=auth_headers(bob)
    )
    assert r.json() == {"status": "error", "message": "No active connections found."}
