"""Two users get separate auto-provisioned workspaces, and neither can
read the other's data — no matter which header they craft.

Covers the core invariant the multi-workspace migration was built for.
The `alice` / `bob` fixtures yield per-test unique user IDs so the
session-scoped DB doesn't leak membership across tests.
"""
from tests.conftest import auth_headers


def test_separate_users_get_separate_workspaces(client, alice, bob):
    # No explicit workspace header — backend auto-provisions Personal.
    a = client.post("/api/chat/conversations", json={}, headers=auth_headers(alice))
    b = client.post("/api/chat/conversations", json={}, headers=auth_headers(bob))
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["id"] != b.json()["id"]


def test_alice_only_sees_her_conversations(client, alice, bob):
    a = client.post("/api/chat/conversations", json={}, headers=auth_headers(alice))
    b = client.post("/api/chat/conversations", json={}, headers=auth_headers(bob))

    a_list = client.get("/api/chat/conversations", headers=auth_headers(alice)).json()
    b_list = client.get("/api/chat/conversations", headers=auth_headers(bob)).json()

    a_ids = {c["id"] for c in a_list}
    b_ids = {c["id"] for c in b_list}
    assert a.json()["id"] in a_ids
    assert b.json()["id"] in b_ids
    assert a_ids.isdisjoint(b_ids)


def test_cross_workspace_read_is_404(client, alice, bob):
    alice_conv = client.post(
        "/api/chat/conversations", json={}, headers=auth_headers(alice)
    ).json()
    # Bob can't read Alice's conversation by ID — workspace-scoped query.
    r = client.get(
        f"/api/chat/conversations/{alice_conv['id']}",
        headers=auth_headers(bob),
    )
    assert r.status_code == 404


def test_spoofing_x_workspace_id_is_403(client, db, alice, bob):
    """Bob explicitly sends Alice's workspace_id. Membership check stops it."""
    # Trigger workspace creation for both
    client.post("/api/chat/conversations", json={}, headers=auth_headers(alice))
    client.post("/api/chat/conversations", json={}, headers=auth_headers(bob))

    from app import models

    alice_ws_id = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
        .id
    )

    r = client.get(
        "/api/chat/conversations",
        headers=auth_headers(bob, workspace_id=alice_ws_id),
    )
    assert r.status_code == 403, r.text
    assert "Not a member" in r.json()["detail"]
