"""Legacy CSV-upload endpoint (`POST /api/upload`).

This endpoint previously referenced an undefined `workspace_id` name (never
took it as a dependency), so every call raised a NameError once it reached
`models.Report(workspace_id=workspace_id, ...)`. It shipped broken because
it had zero test coverage. These tests cover the success path and workspace
scoping now that the dependency has been added.
"""
from io import BytesIO

import pytest

from tests.conftest import auth_headers

CSV_BODY = (
    b"Campaign,Spend,Impressions,Clicks,Conversions,Revenue\n"
    b"Brand-US,1200.50,50000,800,40,8000\n"
)


@pytest.fixture()
def alice_workspace_id(client, db, alice):
    """Ensure Alice has a workspace (auto-created on first authenticated call)."""
    client.get("/api/chat/conversations", headers=auth_headers(alice))
    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    return ws.id


def test_upload_creates_report_scoped_to_workspace(client, db, alice, alice_workspace_id):
    files = {"files": ("google_ads.csv", BytesIO(CSV_BODY), "text/csv")}
    r = client.post(
        "/api/upload",
        files=files,
        headers=auth_headers(alice, alice_workspace_id),
    )
    assert r.status_code == 200

    from app import models

    report = db.query(models.Report).filter(models.Report.user_id == alice).first()
    assert report is not None
    assert report.workspace_id == alice_workspace_id


def test_upload_rejects_non_member_workspace(client, bob, alice_workspace_id):
    """Bob is not a member of Alice's workspace, so spoofing her
    X-Workspace-Id must be rejected before any Report is created."""
    files = {"files": ("google_ads.csv", BytesIO(CSV_BODY), "text/csv")}
    r = client.post(
        "/api/upload",
        files=files,
        headers=auth_headers(bob, alice_workspace_id),
    )
    assert r.status_code == 403
