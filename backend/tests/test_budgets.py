"""Budget pacing: CRUD, role gating, math, and sync-time alert firing
(once per period, deduped via last_alert_period)."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice, bob):
    r = client.post("/api/workspaces", json={"name": "BudgetCo"}, headers=auth_headers(alice))
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


def test_period_bounds_monthly():
    from app.services.budgets import _period_bounds

    ps, pe, label = _period_bounds(
        "monthly", datetime(2026, 5, 15, 12, 0, 0), datetime(2026, 1, 1)
    )
    assert ps == datetime(2026, 5, 1, 0, 0, 0)
    assert pe.date() == datetime(2026, 5, 31).date()
    assert label == "2026-05"


def test_period_bounds_quarterly():
    from app.services.budgets import _period_bounds

    ps, pe, label = _period_bounds(
        "quarterly", datetime(2026, 5, 15), datetime(2026, 1, 1)
    )
    assert ps == datetime(2026, 4, 1, 0, 0, 0)
    assert pe.date() == datetime(2026, 6, 30).date()
    assert label == "2026-Q2"


def test_compute_pacing_on_pace(db, workspace, alice):
    """Half the month elapsed, half the budget spent → on_pace."""
    ws_id = workspace
    from app import models
    from app.services.budgets import compute_pacing

    b = models.Budget(
        workspace_id=ws_id, name="May", scope_type="workspace",
        period_type="monthly", amount="10000",
        start_date=datetime(2026, 5, 1), is_active=1,
        created_by_subject=alice,
    )
    # Half-budget report inside the period.
    r = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 5000},
    )
    r.created_at = datetime(2026, 5, 10)
    db.add_all([b, r])
    db.commit()
    db.refresh(b)

    p = compute_pacing(db, b, now=datetime(2026, 5, 16, 12, 0, 0))  # ~half through May
    assert p["amount"] == 10000.0
    assert p["spent"] == 5000.0
    assert p["status"] == "on_pace"
    assert p["pct_used"] == 0.5


def test_compute_pacing_over_pace(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.budgets import compute_pacing

    b = models.Budget(
        workspace_id=ws_id, name="May", scope_type="workspace",
        period_type="monthly", amount="10000",
        start_date=datetime(2026, 5, 1), is_active=1,
        created_by_subject=alice,
    )
    r = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 8000},
    )
    r.created_at = datetime(2026, 5, 5)
    db.add_all([b, r])
    db.commit()

    p = compute_pacing(db, b, now=datetime(2026, 5, 6, 12, 0, 0))  # ~20% through, 80% spent
    assert p["status"] == "over_pace"


def test_compute_pacing_exhausted(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.budgets import compute_pacing

    b = models.Budget(
        workspace_id=ws_id, name="May", scope_type="workspace",
        period_type="monthly", amount="5000",
        start_date=datetime(2026, 5, 1), is_active=1,
        created_by_subject=alice,
    )
    r = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 6000},
    )
    r.created_at = datetime(2026, 5, 20)
    db.add_all([b, r])
    db.commit()

    p = compute_pacing(db, b, now=datetime(2026, 5, 25))
    assert p["status"] == "exhausted"
    assert p["pct_used"] > 1.0


def test_platform_scoped_budget_sums_only_one_platform(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.budgets import compute_pacing

    b = models.Budget(
        workspace_id=ws_id, name="Google", scope_type="platform", scope_key="google",
        period_type="monthly", amount="5000",
        start_date=datetime(2026, 5, 1), is_active=1,
        created_by_subject=alice,
    )
    r = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 9000},
        platform_summary=[
            {"platform": "google", "spend": 3000},
            {"platform": "meta", "spend": 6000},
        ],
    )
    r.created_at = datetime(2026, 5, 15)
    db.add_all([b, r])
    db.commit()

    p = compute_pacing(db, b, now=datetime(2026, 5, 16))
    assert p["spent"] == 3000  # only google counted


def test_member_can_list_but_not_create(client, workspace, alice, bob):
    ws_id = workspace
    r = client.get(f"/api/workspaces/{ws_id}/budgets", headers=auth_headers(bob, ws_id))
    assert r.status_code == 200

    r = client.post(
        f"/api/workspaces/{ws_id}/budgets",
        json={
            "name": "x", "scope_type": "workspace",
            "period_type": "monthly", "amount": "1000",
        },
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_creates_budget_with_pacing(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/budgets",
        json={
            "name": "May Workspace", "scope_type": "workspace",
            "period_type": "monthly", "amount": "10000",
        },
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "May Workspace"
    assert body["pacing"]["amount"] == 10000.0
    assert body["pacing"]["status"] in {"on_pace", "under_pace", "over_pace", "exhausted"}


def test_create_validates_amount_and_scope(client, workspace, alice):
    ws_id = workspace
    # Negative amount
    r = client.post(
        f"/api/workspaces/{ws_id}/budgets",
        json={
            "name": "x", "scope_type": "workspace",
            "period_type": "monthly", "amount": "-5",
        },
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400

    # Platform scope without scope_key
    r = client.post(
        f"/api/workspaces/{ws_id}/budgets",
        json={
            "name": "x", "scope_type": "platform",
            "period_type": "monthly", "amount": "100",
        },
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_alert_fires_when_threshold_crossed(db, workspace, alice):
    """Budget over 80%, alert_at_pct=80, no prior alert → fires once.
    Re-running the evaluator does not re-fire within the same period."""
    ws_id = workspace
    from app import models
    from app.services.budgets import evaluate_budget_alerts

    b = models.Budget(
        workspace_id=ws_id, name="Tight", scope_type="workspace",
        period_type="monthly", amount="1000",
        start_date=datetime.utcnow().replace(day=1), is_active=1,
        alert_at_pct=80,
        created_by_subject=alice,
    )
    r = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 900},
    )
    db.add_all([b, r])
    db.commit()

    fired = evaluate_budget_alerts(db, workspace_id=ws_id)
    assert len(fired) == 1
    assert fired[0]["pct_used"] >= 0.8

    # Re-running in the same period should be a no-op (dedupe via last_alert_period).
    fired_again = evaluate_budget_alerts(db, workspace_id=ws_id)
    assert fired_again == []


def test_budget_pacing_appears_in_chat_tool(db, workspace, alice):
    """The Gemini tool surface exposes pacing for the chat agent."""
    ws_id = workspace
    from app import models
    from app.services import analytics_tools

    b = models.Budget(
        workspace_id=ws_id, name="May", scope_type="workspace",
        period_type="monthly", amount="5000",
        start_date=datetime.utcnow().replace(day=1), is_active=1,
        created_by_subject=alice,
    )
    db.add(b)
    db.commit()

    rows = analytics_tools.list_budget_pacing(db, ws_id)
    assert len(rows) == 1
    assert rows[0]["name"] == "May"
    assert "status" in rows[0]
    assert rows[0]["amount"] == 5000.0

    # And the declaration is exposed so Gemini can call it.
    assert "list_budget_pacing" in analytics_tools.TOOL_FUNCTIONS
    assert any(d["name"] == "list_budget_pacing" for d in analytics_tools.TOOL_DECLARATIONS)
