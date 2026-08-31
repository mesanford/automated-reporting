"""GET/PUT /api/optimizations/settings.

Regression coverage for a field-name mismatch bug: the Pydantic schemas and
endpoint bodies used `google_chat_webhook_url`, but the real `UserSettings`
model column is `google_chat_webhook`. Because Pydantic silently ignores
unknown fields by default, PUT requests appeared to succeed (200 OK) but
never persisted anything — a plain, non-persisted attribute was being set
on the ORM object instead of the real column. This broke the (already
DB-driven) budget-alert webhook feature in `app/services/budgets.py` and
meant the `notify_new_optimizations` Google Chat notification always fell
back to the single global `GOOGLE_CHAT_WEBHOOK_URL` env var, never the
per-workspace value a user configured in Settings.

These tests cover: the round trip actually persists to the DB column, GET
reflects it back, and `notify_new_optimizations` receives the persisted
per-workspace webhook rather than silently using the env var / doing
nothing.
"""
from unittest.mock import patch

import pytest

from tests.conftest import auth_headers


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


def test_settings_get_defaults_to_none(client, alice, alice_workspace_id):
    r = client.get("/api/optimizations/settings", headers=auth_headers(alice, alice_workspace_id))
    assert r.status_code == 200
    assert r.json()["google_chat_webhook"] is None


def test_settings_put_persists_to_real_column(client, db, alice, alice_workspace_id):
    r = client.put(
        "/api/optimizations/settings",
        json={"google_chat_webhook": "https://chat.googleapis.com/v1/spaces/abc/messages?key=xyz"},
        headers=auth_headers(alice, alice_workspace_id),
    )
    assert r.status_code == 200
    assert r.json()["google_chat_webhook"] == "https://chat.googleapis.com/v1/spaces/abc/messages?key=xyz"

    from app import models

    settings = (
        db.query(models.UserSettings)
        .filter(models.UserSettings.workspace_id == alice_workspace_id)
        .first()
    )
    assert settings is not None
    assert settings.google_chat_webhook == "https://chat.googleapis.com/v1/spaces/abc/messages?key=xyz"

    # GET should now reflect the persisted value, proving it's a real
    # round trip through the DB and not just an echoed request body.
    r2 = client.get("/api/optimizations/settings", headers=auth_headers(alice, alice_workspace_id))
    assert r2.json()["google_chat_webhook"] == "https://chat.googleapis.com/v1/spaces/abc/messages?key=xyz"


def test_notify_new_optimizations_uses_workspace_webhook_not_env(db, alice, alice_workspace_id):
    """The notification path must read the per-workspace DB value, not the
    global env var, once a workspace has configured its own webhook."""
    from app import models
    from app.services.notifications import notify_new_optimizations

    settings = models.UserSettings(
        workspace_id=alice_workspace_id,
        user_id=alice,
        google_chat_webhook="https://chat.googleapis.com/v1/spaces/workspace-specific/messages",
    )
    db.add(settings)
    db.commit()

    plan = models.OptimizationPlan(
        workspace_id=alice_workspace_id,
        user_id=alice,
        connection_id=1,
        platform="google",
        campaign_name="Brand-US",
        change_type="pause",
        proposed_value="paused",
        status="pending",
        is_automated=0,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    with patch("app.services.notifications.send_google_chat_message") as mock_send, \
         patch("app.services.notifications.GOOGLE_CHAT_WEBHOOK_URL", "https://should-not-be-used.example"):
        notify_new_optimizations(
            [plan],
            webhook_url=settings.google_chat_webhook,
        )
        assert mock_send.called
        sent_webhook = mock_send.call_args[0][0]
        assert sent_webhook == "https://chat.googleapis.com/v1/spaces/workspace-specific/messages"
