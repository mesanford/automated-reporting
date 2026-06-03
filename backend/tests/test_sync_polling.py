"""GET /api/sync-jobs/{id} and GET /api/reports/{id} — the two endpoints
the async dashboard cutover relies on. Both are workspace-scoped."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def alice_ws_and_job(client, db, alice):
    """Trigger Alice's Personal workspace, seed a SyncJob + Report."""
    client.get("/api/chat/conversations", headers=auth_headers(alice))

    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    report = models.Report(
        workspace_id=ws.id,
        user_id=alice,
        scorecards={"totalSpend": 123.45},
        gemini_analysis="hello",
    )
    db.add(report)
    db.flush()
    job = models.SyncJob(
        workspace_id=ws.id,
        user_id=alice,
        connection_id=42,
        status="completed",
        progress_percent=100,
        report_id=report.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    db.refresh(report)
    return ws.id, job.id, report.id


def test_get_sync_job_returns_status_and_report_id(client, alice_ws_and_job, alice):
    ws_id, job_id, report_id = alice_ws_and_job
    r = client.get(
        f"/api/sync-jobs/{job_id}",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id
    assert body["status"] == "completed"
    assert body["report_id"] == report_id


def test_get_sync_job_404_when_not_in_workspace(client, alice_ws_and_job, bob):
    """Bob has no membership in Alice's workspace — even spoofing the id
    can't reveal the job. (Without X-Workspace-Id, Bob's request resolves
    to his auto-provisioned Personal workspace, where the job doesn't
    exist.)"""
    _ws_id, job_id, _report_id = alice_ws_and_job
    r = client.get(
        f"/api/sync-jobs/{job_id}",
        headers=auth_headers(bob),
    )
    assert r.status_code == 404


def test_get_report_returns_full_payload(client, alice_ws_and_job, alice):
    ws_id, _job_id, report_id = alice_ws_and_job
    r = client.get(
        f"/api/reports/{report_id}",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == report_id
    assert body["scorecards"]["totalSpend"] == 123.45
    assert body["geminiAnalysis"] == "hello"
    # Shape promise: same keys the synchronous /sync endpoint historically
    # returned in its envelope.
    for key in (
        "chartData", "scorecards", "scorecardDeltas", "platformDeltas",
        "campaignSummary", "hierarchySummary", "platformSummary",
        "topPerformer", "bottomPerformer", "geminiAnalysis",
    ):
        assert key in body


def test_get_report_404_for_wrong_workspace(client, alice_ws_and_job, bob):
    _ws_id, _job_id, report_id = alice_ws_and_job
    r = client.get(
        f"/api/reports/{report_id}",
        headers=auth_headers(bob),
    )
    assert r.status_code == 404
