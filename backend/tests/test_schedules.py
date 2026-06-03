"""Scheduled syncs: CRUD with role gating, plus the scheduler tick that
picks due rows and enqueues sync-all for each."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, db, alice, bob):
    """Alice owns the workspace; Bob is a member."""
    r = client.post(
        "/api/workspaces", json={"name": "Sched Co"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]
    inv = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": f"{bob}@x.com", "role": "member"},
        headers=auth_headers(alice, ws_id),
    ).json()
    client.post(
        f"/api/workspaces/invites/accept?token={inv['token']}",
        headers=auth_headers(bob),
    )
    return ws_id


def test_compute_next_run_at_daily():
    from app.services.scheduling import compute_next_run_at

    n = compute_next_run_at(
        frequency="daily", hour_utc=14, day_of_week=None,
        now=datetime(2026, 6, 3, 10, 0, 0),
    )
    assert n == datetime(2026, 6, 3, 14, 0, 0)

    n = compute_next_run_at(
        frequency="daily", hour_utc=14, day_of_week=None,
        now=datetime(2026, 6, 3, 18, 0, 0),
    )
    assert n == datetime(2026, 6, 4, 14, 0, 0)


def test_compute_next_run_at_weekly():
    from app.services.scheduling import compute_next_run_at

    n = compute_next_run_at(
        frequency="weekly", hour_utc=9, day_of_week=0,
        now=datetime(2026, 6, 3, 10, 0, 0),  # Wed
    )
    assert n.weekday() == 0
    assert n.hour == 9


def test_member_can_list_but_not_create(client, workspace, alice, bob):
    ws_id = workspace
    r = client.get(f"/api/workspaces/{ws_id}/schedules", headers=auth_headers(bob, ws_id))
    assert r.status_code == 200
    assert r.json() == []

    r = client.post(
        f"/api/workspaces/{ws_id}/schedules",
        json={"name": "Daily", "frequency": "daily", "hour_utc": 14},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_can_create_and_list(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/schedules",
        json={"name": "Morning", "frequency": "daily", "hour_utc": 13},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Morning"
    assert body["next_run_at"] is not None


def test_create_validates_inputs(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/schedules",
        json={"name": "Bad", "frequency": "weekly", "hour_utc": 14},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_update_changes_next_run_when_cadence_changes(client, db, workspace, alice):
    ws_id = workspace
    sched = client.post(
        f"/api/workspaces/{ws_id}/schedules",
        json={"name": "X", "frequency": "daily", "hour_utc": 10},
        headers=auth_headers(alice, ws_id),
    ).json()
    initial_next = sched["next_run_at"]

    updated = client.patch(
        f"/api/workspaces/{ws_id}/schedules/{sched['id']}",
        json={"hour_utc": 22},
        headers=auth_headers(alice, ws_id),
    ).json()
    assert updated["hour_utc"] == 22
    assert updated["next_run_at"] != initial_next


def test_delete(client, workspace, alice):
    ws_id = workspace
    sched = client.post(
        f"/api/workspaces/{ws_id}/schedules",
        json={"name": "X", "frequency": "daily", "hour_utc": 10},
        headers=auth_headers(alice, ws_id),
    ).json()
    r = client.delete(
        f"/api/workspaces/{ws_id}/schedules/{sched['id']}",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200


def test_tick_enqueues_only_due_schedules(client, db, workspace, alice, monkeypatch):
    ws_id = workspace
    monkeypatch.setenv("ALLOW_INTERNAL_NO_OIDC", "1")

    from app import models

    past = datetime.utcnow() - timedelta(hours=1)
    future = datetime.utcnow() + timedelta(hours=1)
    due = models.ScheduledSync(
        workspace_id=ws_id, name="Due", frequency="daily", hour_utc=10,
        is_active=1, created_by_subject=alice, next_run_at=past,
    )
    not_due = models.ScheduledSync(
        workspace_id=ws_id, name="Later", frequency="daily", hour_utc=10,
        is_active=1, created_by_subject=alice, next_run_at=future,
    )
    db.add_all([due, not_due])
    db.commit()
    db.refresh(due)
    due_id = due.id
    not_due_id = not_due.id

    r = client.post("/api/internal/scheduler/tick", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["enqueued"]) == 1
    assert body["enqueued"][0]["schedule_id"] == due_id

    db.expire_all()
    due_after = db.query(models.ScheduledSync).filter(models.ScheduledSync.id == due_id).first()
    not_due_after = db.query(models.ScheduledSync).filter(models.ScheduledSync.id == not_due_id).first()
    assert due_after.last_run_at is not None
    assert due_after.next_run_at > datetime.utcnow()
    assert not_due_after.last_run_at is None


def test_tick_skips_inactive_schedules(client, db, workspace, alice, monkeypatch):
    ws_id = workspace
    monkeypatch.setenv("ALLOW_INTERNAL_NO_OIDC", "1")

    from app import models

    inactive = models.ScheduledSync(
        workspace_id=ws_id, name="Paused", frequency="daily", hour_utc=10,
        is_active=0, created_by_subject=alice,
        next_run_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(inactive)
    db.commit()

    r = client.post("/api/internal/scheduler/tick", json={})
    assert r.status_code == 200
    assert r.json()["enqueued"] == []
