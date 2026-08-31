"""Saved views: per-user + workspace-shared. Default uniqueness per
(workspace, user). Edits gated on author or admin."""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice, bob):
    r = client.post("/api/workspaces", json={"name": "ViewCo"}, headers=auth_headers(alice))
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


def test_member_creates_private_view(client, workspace, bob):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "My CPA view", "config": {"focus": "cpa"}},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "My CPA view"
    assert body["visibility"] == "private"
    assert body["user_id"] == bob


def test_member_cannot_publish_workspace_view(client, workspace, bob):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "shared", "visibility": "workspace"},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_publishes_workspace_view(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "Team default", "visibility": "workspace"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert r.json()["visibility"] == "workspace"


def test_member_sees_their_private_and_workspace_shared(client, workspace, alice, bob):
    ws_id = workspace
    client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "alice private"},
        headers=auth_headers(alice, ws_id),
    )
    client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "team", "visibility": "workspace"},
        headers=auth_headers(alice, ws_id),
    )
    client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "bob private"},
        headers=auth_headers(bob, ws_id),
    )

    rows = client.get(
        f"/api/workspaces/{ws_id}/views", headers=auth_headers(bob, ws_id)
    ).json()
    names = {r["name"] for r in rows}
    assert "bob private" in names
    assert "team" in names
    assert "alice private" not in names  # private to alice


def test_setting_default_clears_prior_default_for_same_user(client, workspace, bob):
    """Each (workspace, user) has at most one default view."""
    ws_id = workspace
    a = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "first", "is_default": True},
        headers=auth_headers(bob, ws_id),
    ).json()
    b = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "second", "is_default": True},
        headers=auth_headers(bob, ws_id),
    ).json()

    rows = client.get(
        f"/api/workspaces/{ws_id}/views", headers=auth_headers(bob, ws_id)
    ).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[a["id"]]["is_default"] is False
    assert by_id[b["id"]]["is_default"] is True


def test_author_can_edit_private_view(client, workspace, bob):
    ws_id = workspace
    v = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "v"}, headers=auth_headers(bob, ws_id),
    ).json()
    r = client.patch(
        f"/api/workspaces/{ws_id}/views/{v['id']}",
        json={"name": "renamed"}, headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"


def test_admin_can_edit_others_workspace_view(client, workspace, alice, bob):
    """Bob (member) publishes nothing himself, but if Alice publishes a
    workspace view, she (admin) can edit it. A peer member could not."""
    ws_id = workspace
    v = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "alice-team", "visibility": "workspace"},
        headers=auth_headers(alice, ws_id),
    ).json()
    # Alice can edit her own:
    r = client.patch(
        f"/api/workspaces/{ws_id}/views/{v['id']}",
        json={"name": "alice-renamed"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    # Bob (member) cannot edit it:
    r = client.patch(
        f"/api/workspaces/{ws_id}/views/{v['id']}",
        json={"name": "bob-touched"},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_delete_requires_author_or_admin(client, workspace, alice, bob):
    ws_id = workspace
    v = client.post(
        f"/api/workspaces/{ws_id}/views",
        json={"name": "alice-priv"},
        headers=auth_headers(alice, ws_id),
    ).json()
    # Bob can't delete Alice's private view.
    r = client.delete(
        f"/api/workspaces/{ws_id}/views/{v['id']}",
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code in (403, 404)  # bob couldn't see it either via list
