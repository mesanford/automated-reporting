"""Invite creation runs the email_service; response always carries
`accept_url` + `email_delivery` so the UI can decide between the
"emailed" and "copy this link" affordances."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice):
    """Alice creates a fresh workspace; return its id."""
    r = client.post(
        "/api/workspaces", json={"name": "InviteCo"}, headers=auth_headers(alice)
    )
    return r.json()["id"]


def test_response_includes_accept_url_and_delivery(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": "newbie@x.com", "role": "member"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert "accept_url" in body
    assert body["accept_url"].endswith(f"token={body['token']}")
    assert body["email_delivery"]["provider"] == "noop"
    assert body["email_delivery"]["delivered"] is False
    assert body["email_delivery"]["error"] is None


def test_email_service_called_with_workspace_name(client, workspace, alice, monkeypatch):
    """The handler should pass the workspace's real name + role to the
    email layer so the rendered subject/body identifies the workspace."""
    ws_id = workspace
    calls: list[dict] = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return {"delivered": True, "provider": "stub", "error": None}

    # The handler imported `send_invite_email` into workspaces.py at module
    # load, so we patch the symbol the handler actually references.
    import app.api.workspaces as ws_mod

    monkeypatch.setattr(ws_mod, "send_invite_email", fake_send)

    r = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": "TEAMMATE@X.COM", "role": "viewer"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert r.json()["email_delivery"]["delivered"] is True
    assert r.json()["email_delivery"]["provider"] == "stub"

    assert len(calls) == 1
    sent = calls[0]
    assert sent["to_email"] == "teammate@x.com"  # backend lowercases
    assert sent["workspace_name"] == "InviteCo"
    assert sent["role"] == "viewer"
    assert "token=" in sent["accept_url"]


def test_render_invite_body_contains_expected_fields():
    """Direct unit test of the body renderer — no FastAPI involved."""
    from datetime import datetime

    from app.services.email_service import _render_invite_body

    subject, body = _render_invite_body(
        accept_url="https://app.example.com/invites/accept?token=abc",
        workspace_name="Acme",
        role="admin",
        expires_at=datetime(2026, 7, 1, 12, 0, 0),
        invited_by="alice@acme.com",
    )
    assert "Acme" in subject
    assert "alice@acme.com" in body
    assert "admin" in body
    assert "https://app.example.com/invites/accept?token=abc" in body
    assert "2026-07-01" in body
