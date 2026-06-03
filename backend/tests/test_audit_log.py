"""Consequential workspace actions land in the audit log."""
from tests.conftest import auth_headers


def test_invite_and_role_change_recorded(client, alice, bob):
    r = client.post(
        "/api/workspaces", json={"name": "Audited Inc"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]
    H_ALICE = auth_headers(alice, ws_id)

    invite = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": f"{bob}@x.com", "role": "member"},
        headers=H_ALICE,
    ).json()
    client.post(
        f"/api/workspaces/invites/accept?token={invite['token']}",
        headers=auth_headers(bob),
    )

    client.patch(
        f"/api/workspaces/{ws_id}/members/{bob}",
        json={"role": "admin"},
        headers=H_ALICE,
    )

    log = client.get(
        f"/api/workspaces/{ws_id}/audit-log", headers=H_ALICE
    ).json()
    actions = [e["action"] for e in log]

    assert "invite.create" in actions
    assert "invite.accept" in actions
    assert "member.role_change" in actions

    role_change = next(e for e in log if e["action"] == "member.role_change")
    assert role_change["actor_subject"] == alice
    assert role_change["payload"]["from"] == "member"
    assert role_change["payload"]["to"] == "admin"


def test_audit_log_ordered_newest_first(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "Ordered Inc"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]
    H = auth_headers(alice, ws_id)

    # Two invites; the second one should appear before the first in the log.
    client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": "first@x.com", "role": "member"},
        headers=H,
    )
    client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": "second@x.com", "role": "viewer"},
        headers=H,
    )

    log = client.get(f"/api/workspaces/{ws_id}/audit-log", headers=H).json()
    invite_emails_in_order = [
        e["payload"]["email"]
        for e in log
        if e["action"] == "invite.create"
    ]
    assert invite_emails_in_order[:2] == ["second@x.com", "first@x.com"]
