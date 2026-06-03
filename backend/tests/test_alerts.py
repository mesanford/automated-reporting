"""Alert rules: CRUD, role gating, evaluator fires when thresholds breach."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice, bob):
    r = client.post("/api/workspaces", json={"name": "Alert Co"}, headers=auth_headers(alice))
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


def test_member_can_list_but_not_create(client, workspace, alice, bob):
    ws_id = workspace
    r = client.get(f"/api/workspaces/{ws_id}/alerts", headers=auth_headers(bob, ws_id))
    assert r.status_code == 200

    r = client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={"name": "CPA spike", "metric": "blendedCPA", "comparison": "gt", "threshold": "50"},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_creates_rule(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={"name": "CPA spike", "metric": "blendedCPA", "comparison": "gt", "threshold": "50"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "CPA spike"


def test_create_validates_comparison_and_threshold(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={"name": "x", "metric": "spend", "comparison": "bogus", "threshold": "5"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400

    r = client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={"name": "x", "metric": "spend", "comparison": "gt", "threshold": "abc"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_evaluator_fires_on_gt_breach(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.alerts import evaluate_rules_for_report

    rule = models.AlertRule(
        workspace_id=ws_id, name="CPA>50", metric="blendedCPA",
        comparison="gt", threshold="50", channels=[],
        is_active=1, created_by_subject=alice,
    )
    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"blendedCPA": 75.0},
    )
    db.add_all([rule, report])
    db.commit()
    db.refresh(report)
    db.refresh(rule)

    triggered = evaluate_rules_for_report(db, workspace_id=ws_id, report=report)
    assert len(triggered) == 1
    assert triggered[0]["observed"] == 75.0

    db.expire_all()
    rule = db.query(models.AlertRule).filter(models.AlertRule.id == rule.id).first()
    assert rule.last_triggered_at is not None
    assert rule.last_value == "75.0"


def test_evaluator_no_fire_when_below(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.alerts import evaluate_rules_for_report

    rule = models.AlertRule(
        workspace_id=ws_id, name="CPA>50", metric="blendedCPA",
        comparison="gt", threshold="50", channels=[],
        is_active=1, created_by_subject=alice,
    )
    report = models.Report(
        workspace_id=ws_id, user_id=alice, scorecards={"blendedCPA": 30.0},
    )
    db.add_all([rule, report])
    db.commit()
    db.refresh(report)

    triggered = evaluate_rules_for_report(db, workspace_id=ws_id, report=report)
    assert triggered == []


def test_evaluator_skips_inactive_rules(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.alerts import evaluate_rules_for_report

    rule = models.AlertRule(
        workspace_id=ws_id, name="paused", metric="blendedCPA",
        comparison="gt", threshold="0", channels=[],
        is_active=0, created_by_subject=alice,
    )
    report = models.Report(
        workspace_id=ws_id, user_id=alice, scorecards={"blendedCPA": 999.0},
    )
    db.add_all([rule, report])
    db.commit()
    db.refresh(report)

    assert evaluate_rules_for_report(db, workspace_id=ws_id, report=report) == []


def test_evaluator_pct_change_uses_delta(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.alerts import evaluate_rules_for_report

    rule = models.AlertRule(
        workspace_id=ws_id, name="spike", metric="blendedCPA",
        comparison="pct_change_gt", threshold="10", channels=[],
        is_active=1, created_by_subject=alice,
    )
    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"blendedCPA": 100.0},
        scorecard_deltas={"blendedCPA": {"value": "+25.0%", "direction": "negative"}},
    )
    db.add_all([rule, report])
    db.commit()
    db.refresh(report)

    triggered = evaluate_rules_for_report(db, workspace_id=ws_id, report=report)
    assert len(triggered) == 1
    assert triggered[0]["observed"] == 25.0


def test_alert_trigger_writes_audit_entry(client, db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.alerts import evaluate_rules_for_report

    rule = models.AlertRule(
        workspace_id=ws_id, name="any", metric="totalSpend",
        comparison="gt", threshold="0", channels=[],
        is_active=1, created_by_subject=alice,
    )
    report = models.Report(
        workspace_id=ws_id, user_id=alice, scorecards={"totalSpend": 100.0},
    )
    db.add_all([rule, report])
    db.commit()
    db.refresh(report)

    evaluate_rules_for_report(db, workspace_id=ws_id, report=report)

    log = client.get(
        f"/api/workspaces/{ws_id}/audit-log", headers=auth_headers(alice, ws_id)
    ).json()
    actions = [e["action"] for e in log]
    assert "alert.trigger" in actions
