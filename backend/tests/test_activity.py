"""Workspace activity feed: unions audit_log + completed SyncJobs + Reports
into one chronological timeline."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice, bob):
    r = client.post(
        "/api/workspaces", json={"name": "ActCo"}, headers=auth_headers(alice)
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


def test_audit_only_returns_audit_kind(client, workspace, alice):
    """Fresh workspace already has invite.create + invite.accept rows."""
    ws_id = workspace
    rows = client.get(
        f"/api/workspaces/{ws_id}/activity", headers=auth_headers(alice, ws_id)
    ).json()
    assert len(rows) >= 2
    kinds = {r["kind"] for r in rows}
    assert "audit" in kinds


def test_unifies_audit_and_sync_and_report(client, db, workspace, alice):
    ws_id = workspace
    from app import models

    # Seed a completed SyncJob and a Report.
    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 1234, "totalConversions": 56},
        current_period_label="2026-05",
    )
    db.add(report)
    db.flush()
    job = models.SyncJob(
        workspace_id=ws_id, user_id=alice, connection_id=None,
        status="completed", progress_percent=100,
        report_id=report.id, completed_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()

    rows = client.get(
        f"/api/workspaces/{ws_id}/activity", headers=auth_headers(alice, ws_id)
    ).json()
    kinds = {r["kind"] for r in rows}
    assert {"audit", "sync", "report"}.issubset(kinds)

    sync_event = next(r for r in rows if r["kind"] == "sync")
    assert sync_event["status"] == "completed"
    assert sync_event["report_id"] == report.id

    report_event = next(r for r in rows if r["kind"] == "report")
    assert report_event["report_id"] == report.id
    assert report_event["total_spend"] == 1234
    assert report_event["period_label"] == "2026-05"


def test_ordered_newest_first(client, db, workspace, alice):
    """Two reports with explicit timestamps; later one comes first."""
    ws_id = workspace
    from app import models

    older = models.Report(workspace_id=ws_id, user_id=alice, scorecards={})
    older.created_at = datetime.utcnow() - timedelta(hours=2)
    newer = models.Report(workspace_id=ws_id, user_id=alice, scorecards={})
    newer.created_at = datetime.utcnow() - timedelta(minutes=5)
    db.add_all([older, newer])
    db.commit()

    rows = client.get(
        f"/api/workspaces/{ws_id}/activity", headers=auth_headers(alice, ws_id)
    ).json()
    report_events = [r for r in rows if r["kind"] == "report"]
    assert report_events[0]["report_id"] == newer.id
    assert report_events[1]["report_id"] == older.id


def test_workspace_isolation(client, db, workspace, alice, bob):
    """Bob is a member of `workspace`; events from a *separate* workspace
    of his own should not leak in."""
    ws_id = workspace

    # Bob's own workspace + a report there.
    r = client.post(
        "/api/workspaces", json={"name": "Bob Co"}, headers=auth_headers(bob)
    )
    bob_ws = r.json()["id"]
    from app import models
    other_report = models.Report(workspace_id=bob_ws, user_id=bob, scorecards={"totalSpend": 999})
    db.add(other_report)
    db.commit()

    rows = client.get(
        f"/api/workspaces/{ws_id}/activity", headers=auth_headers(alice, ws_id)
    ).json()
    # No report_event should reference the foreign report id.
    assert all(
        r.get("report_id") != other_report.id
        for r in rows
        if r["kind"] in {"report", "sync"}
    )


def test_non_member_gets_403(client, workspace, carol):
    ws_id = workspace
    r = client.get(
        f"/api/workspaces/{ws_id}/activity", headers=auth_headers(carol)
    )
    assert r.status_code == 403


def test_limit_clamped_to_max_200(client, workspace, alice):
    """`limit` query is sanity-capped server-side."""
    ws_id = workspace
    r = client.get(
        f"/api/workspaces/{ws_id}/activity?limit=99999",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert len(r.json()) <= 200
