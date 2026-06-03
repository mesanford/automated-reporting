"""CSV export of a report's campaign-level data."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace_with_report(client, db, alice):
    """Create Alice's workspace and seed a Report with campaign rows."""
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
        current_period_label="2026-05",
        campaign_summary=[
            {
                "platform": "google", "campaign": "Brand-US",
                "spend": 1200.5, "impressions": 50000, "clicks": 800,
                "conversions": 40, "revenue": 8000,
                "cpa": 30.0, "ctr": 1.6, "cvr": 5.0,
                "cpc": 1.5, "cpm": 24, "roas": 6.66, "spend_share": 60.0,
            },
            {
                "platform": "meta", "campaign": "Prospecting",
                "spend": 800, "impressions": 40000, "clicks": 600,
                "conversions": 20, "revenue": 2000,
                "cpa": 40.0, "ctr": 1.5, "cvr": 3.3,
                "cpc": 1.33, "cpm": 20, "roas": 2.5, "spend_share": 40.0,
            },
        ],
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return ws.id, report.id


def test_csv_export_returns_csv_with_headers(client, workspace_with_report, alice):
    ws_id, report_id = workspace_with_report
    r = client.get(
        f"/api/reports/{report_id}/csv",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert f"report-{report_id}" in r.headers["content-disposition"]

    body = r.text
    lines = body.strip().splitlines()
    assert lines[0] == (
        "platform,campaign,spend,impressions,clicks,conversions,"
        "revenue,cpa,ctr,cvr,cpc,cpm,roas,spend_share"
    )
    assert len(lines) == 3  # header + 2 rows
    assert "Brand-US" in lines[1]
    assert "Prospecting" in lines[2]


def test_csv_export_404_for_other_workspace(client, workspace_with_report, bob):
    """Bob has no membership in Alice's workspace."""
    _ws_id, report_id = workspace_with_report
    r = client.get(
        f"/api/reports/{report_id}/csv",
        headers=auth_headers(bob),
    )
    assert r.status_code == 404


def test_csv_export_handles_empty_campaign_summary(client, db, alice):
    """A Report with no campaign rows still returns just the header."""
    client.get("/api/chat/conversations", headers=auth_headers(alice))
    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    report = models.Report(
        workspace_id=ws.id, user_id=alice, campaign_summary=[],
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    r = client.get(
        f"/api/reports/{report.id}/csv",
        headers=auth_headers(alice, ws.id),
    )
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert len(lines) == 1  # header only
