"""Webhook URLs and email targets on alert channels are secrets: encrypted
at rest, masked on the API surface, decrypted only at delivery time."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "Sec Co"}, headers=auth_headers(alice)
    )
    return r.json()["id"]


def test_channel_url_is_encrypted_at_rest(client, db, workspace, alice):
    ws_id = workspace
    secret_url = "https://hooks.slack.com/services/T000/B000/very-secret-token"
    client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={
            "name": "CPA spike",
            "metric": "blendedCPA",
            "comparison": "gt",
            "threshold": "50",
            "channels": [{"type": "slack", "url": secret_url}],
        },
        headers=auth_headers(alice, ws_id),
    )

    from app import models

    rule = db.query(models.AlertRule).filter(models.AlertRule.workspace_id == ws_id).first()
    stored = rule.channels[0]["url"]
    assert stored != secret_url
    # `v1:` or `v2:` ciphertext prefix from security.py.
    assert stored.startswith(("v1:", "v2:"))


def test_api_response_masks_channel_url(client, workspace, alice):
    ws_id = workspace
    secret_url = "https://hooks.slack.com/services/T000/B000/secret"
    rule = client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={
            "name": "x",
            "metric": "totalSpend",
            "comparison": "gt",
            "threshold": "100",
            "channels": [{"type": "slack", "url": secret_url}],
        },
        headers=auth_headers(alice, ws_id),
    ).json()
    # Masked in the create response.
    assert rule["channels"][0]["url"] == "***"

    # Masked in the list response.
    rows = client.get(
        f"/api/workspaces/{ws_id}/alerts", headers=auth_headers(alice, ws_id)
    ).json()
    assert rows[0]["channels"][0]["url"] == "***"


def test_decrypt_for_delivery_recovers_plaintext(client, db, workspace, alice):
    ws_id = workspace
    secret = "https://hooks.slack.com/services/X/Y/Z-plain"
    client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={
            "name": "x",
            "metric": "totalSpend",
            "comparison": "gt",
            "threshold": "1",
            "channels": [{"type": "slack", "url": secret}],
        },
        headers=auth_headers(alice, ws_id),
    )

    from app import models
    from app.api.alerts import decrypt_channels_for_delivery

    rule = db.query(models.AlertRule).filter(models.AlertRule.workspace_id == ws_id).first()
    decrypted = decrypt_channels_for_delivery(rule.channels)
    assert decrypted[0]["url"] == secret


def test_evaluator_sends_to_decrypted_url(client, db, workspace, alice, monkeypatch):
    """End-to-end: alert fires, the notification call receives the plaintext URL,
    not the stored ciphertext."""
    ws_id = workspace
    secret = "https://hooks.slack.com/services/A/B/C"
    client.post(
        f"/api/workspaces/{ws_id}/alerts",
        json={
            "name": "any spend",
            "metric": "totalSpend",
            "comparison": "gt",
            "threshold": "0",
            "channels": [{"type": "slack", "url": secret}],
        },
        headers=auth_headers(alice, ws_id),
    )

    from app import models
    from app.services import alerts as alerts_svc
    from app.services import notifications

    captured = []

    def fake_send_to_channel(channel, subject, body):
        captured.append(channel)
        return {"delivered": True, "channel": channel["type"], "error": None}

    monkeypatch.setattr(notifications, "send_to_channel", fake_send_to_channel)

    report = models.Report(
        workspace_id=ws_id, user_id=alice, scorecards={"totalSpend": 1234.56}
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    alerts_svc.evaluate_rules_for_report(db, workspace_id=ws_id, report=report)

    assert len(captured) == 1
    assert captured[0]["url"] == secret  # decrypted before delivery
